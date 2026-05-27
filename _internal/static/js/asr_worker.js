/**
 * MATRIX AI INDUSTRIAL STANDARDS - WORKER CONDUCT:
 * 1. INPUT VALIDATION: Always cross-check incoming samples vs duration to verify 16kHz.
 * 2. HEARTBEAT: Maintain a 5s-10s visual heartbeat during inference to prevent 'stuck' UX.
 * 3. ERROR TRANSPARENCY: Wrap all logic in try-catch and report stack details to main thread.
 */
// Web Worker (module type) - 离线 Whisper 诊断版
import { pipeline, env } from '/static/js/transformers.min.js';

const origin = self.location.origin;

env.allowRemoteModels = true;
env.allowLocalModels = true;
env.localModelPath = `${origin}/models/`; 
env.backends.onnx.wasm.wasmPaths = `${origin}/static/js/`;
env.backends.onnx.wasm.numThreads = 4; // 限制并行线程数，防止冷启动衍生过多进程

let asr = null;

const fmtTime = (s) => {
    const min = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${min.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
};

console.log('[Worker] Diagnostic Pipeline v4.5 Online', { origin, localModelPath: env.localModelPath });
self.postMessage({ type: 'status', text: 'AI Engine Diagnostic Pipeline v4.5 Online' });

self.onmessage = async (e) => {
    try {
        const { type, payload } = e.data;

        if (type === 'init') {
            const modelId = payload?.model_id || 'Xenova/whisper-small';
            const isQuantized = payload?.quantized !== false;
            const device = payload?.device || 'wasm';
            const dtype = payload?.dtype || 'fp32';
            const allowRemote = payload?.allowRemote === true;
            
            // 权限审计：正式生产阶段严禁开启联网，以此固化环境
            env.allowRemoteModels = allowRemote;
            
            self.postMessage({ type: 'status', text: `正在唤醒语义引擎 (${modelId.split('/').pop().toUpperCase()}) @ ${device.toUpperCase()}...` });
            try {
                asr = await pipeline('automatic-speech-recognition', modelId, {
                    device: device,
                    quantized: isQuantized,
                    dtype: dtype,
                    progress_callback: (p) => {
                        if (p.status === 'initiate') {
                            self.postMessage({ type: 'status', text: `文件加载中: ${p.file || '...'}` });
                        } else if (p.status === 'download') {
                            self.postMessage({ type: 'status', text: `正在读取模型: ${p.file}` });
                        } else if (p.status === 'progress') {
                            self.postMessage({ type: 'progress', pct: p.progress, file: p.file });
                        } else if (p.status === 'done') {
                            self.postMessage({ type: 'status', text: `准备就绪: ${p.file}` });
                        }
                    }
                });
                self.postMessage({ type: 'ready', device: device }); // Send back actual used device
                self.postMessage({ type: 'status', text: `引擎已就绪 [Mode: ${device.toUpperCase()} | dtype: ${dtype}]` });
            } catch (err) {
                self.postMessage({ type: 'error', msg: `Init Failed: ${err.message}` });
            }
        }

        if (type === 'transcribe') {
            let heartbeat = null;
            try {
                // 1. 物理信号：打破 30 秒沉默的黑盒
                self.postMessage({ type: 'status', text: '[Audit] Worker: Payload received. Starting industrial pipeline...' });
                
                const { audio, totalDuration } = payload || {};
                if (!audio || !audio.length) throw new Error("Worker Error: Received empty or invalid audio buffer (Transfer failed).");
                if (!asr) throw new Error("Worker Error: AI Engine is offline or not initialized.");

                const samplesCount = audio.length;
                const safeDur = (totalDuration && !isNaN(totalDuration)) ? totalDuration : (samplesCount / 16000);
                const inferredRate = Math.round(samplesCount / safeDur);
                
                self.postMessage({ 
                    type: 'status', 
                    text: `[Audit] Ingest Audit: ${samplesCount} samples | Rate: ${inferredRate}Hz | Dur: ${safeDur.toFixed(1)}s` 
                });

                if (Math.abs(inferredRate - 16000) > 2) {
                    self.postMessage({ type: 'error', msg: `Critical Audit Fail: Sample Rate Mismatch (${inferredRate}Hz vs 16000Hz). Check resampler.` });
                }

                statusIcon(`● 引擎分段对齐中 (Total: ${safeDur.toFixed(1)}s)`);

                const CHUNK_SIZE_S = payload.config?.segment_length_s || 30; // 动态读取
                const OVERLAP_S = payload.config?.segment_overlap_s || 2;    // 动态读取
                const sampleRate = 16000;
                
                let allChunks = [];
                let processedSec = 0;

                // 启动分段步进推理
                for (let startSec = 0; startSec < safeDur; startSec += (CHUNK_SIZE_S - OVERLAP_S)) {
                    let endSec = Math.min(startSec + CHUNK_SIZE_S, safeDur);
                    let startSample = Math.floor(startSec * sampleRate);
                    let endSample = Math.floor(endSec * sampleRate);
                    
                    const audioSlice = audio.slice(startSample, endSample);
                    const timerStr = `[${fmtTime(startSec)} / ${fmtTime(safeDur)}]`;
                    self.postMessage({ type: 'status', text: `ASR 中文语义识中 ${timerStr} (深度处理)` });

                    const result = await asr(audioSlice, {
                        language: payload.config?.language || 'chinese',
                        task: payload.config?.task || 'transcribe',
                        return_timestamps: true,
                    });

                    if (result && result.chunks) {
                        // 时间戳修正：将分段内的相对时间转换为全局时间
                        result.chunks.forEach(c => {
                            const globalStart = c.timestamp[0] + startSec;
                            const globalEnd = c.timestamp[1] + startSec;
                            
                            // 过滤掉重叠部分的重复文字（简单逻辑：只保留本段核心区域的文字）
                            if (globalStart >= processedSec) {
                                self.postMessage({ 
                                    type: 'chunk', 
                                    text: c.text.trim(), 
                                    time: globalStart 
                                });
                                allChunks.push({
                                    text: c.text,
                                    timestamp: [globalStart, globalEnd]
                                });
                            }
                        });
                    }
                    processedSec = endSec - OVERLAP_S;
                    
                    // 给事件循环一个喘息的机会，确保 postMessage 能发出去
                    await new Promise(r => setTimeout(r, 10));
                }

                self.postMessage({ type: 'status', text: '[Audit] PIPELINE: Manual segmented ASR completed.' });
                self.postMessage({ type: 'done', result: { text: allChunks.map(c => c.text).join(' '), chunks: allChunks } });
            } catch (err) {
                console.error(err);
                const errorPayload = { 
                    type: 'error', 
                    msg: `ASR 运行异常: ${err.message}`, 
                    details: err.stack,
                    audioLength: payload?.audio?.length || 0
                };
                self.postMessage(errorPayload);
            } finally {
                // Heartbeat no longer strictly needed due to loop-based status updates
            }
        }
    } catch (fatalErr) {
        self.postMessage({ 
            type: 'error', 
            msg: `Worker 严重故障: ${fatalErr.message}`,
            details: fatalErr.stack
        });
    }
};

function statusIcon(txt) {
    self.postMessage({ type: 'status', text: txt });
}
