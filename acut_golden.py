"""
[AI Gold Segment Selector]
Modified to use DeepSeek API for seed-video style selection.
"""

import typing
from acut_deepseek import deepseek_elite_selector

def ai_elite_selector(segments: list, target_duration: int = 90, high_score_theme: str = "", low_score_theme: str = "", config: typing.Optional[dict] = None, project_id: typing.Optional[str] = None, workdir: typing.Optional[str] = None, log_fn: typing.Optional[typing.Callable] = None) -> list:
    """
    [AI Elite Selector v3.2]
    Delegates to DeepSeek API for high-quality seed-video selection.
    """
    if not segments:
        return []

    return deepseek_elite_selector(
        segments, 
        target_duration=target_duration, 
        high_score_theme=high_score_theme, 
        low_score_theme=low_score_theme,
        config=config, 
        project_id=project_id,
        workdir=workdir,
        log_fn=log_fn
    )
