#!/usr/bin/env bash
# =============================================================================
# AutoCut - GitHub Actions M-Chip Release Trigger Script
# =============================================================================
set -e

# 1. 检查 Git 仓库初始化状态
if [ ! -d ".git" ]; then
    echo "[Git] 检测到当前目录尚未初始化 Git，正在为您初始化..."
    git init
    # 默认分支设为 main
    git checkout -b main 2>/dev/null || git branch -M main
fi

# 2. 检查并绑定远程 origin 地址
if ! git remote get-url origin &>/dev/null; then
    echo "=========================================================="
    echo "⚠️  未检测到 Git 远程仓库 (origin) 地址。"
    echo "请在下方输入您的 GitHub 仓库地址"
    echo "（支持 HTTPS 或 SSH 格式，例如: https://github.com/您的用户名/您的仓库名.git）"
    echo "=========================================================="
    read -p "请输入仓库地址: " REMOTE_URL
    if [ -n "$REMOTE_URL" ]; then
        git remote add origin "$REMOTE_URL"
        echo "[Git] 成功绑定远程仓库: $REMOTE_URL"
    else
        echo "[ERROR] 未提供仓库地址，脚本终止。"
        exit 1
    fi
fi

# 3. 获取版本号并创建对应的 Release Tag
VERSION="12.93"
if [ -f "VERSION" ]; then
    CONF_VER=$(cat VERSION | tr -d '[:space:]')
    if [ -n "$CONF_VER" ]; then
        VERSION="$CONF_VER"
    fi
fi

TAG_NAME="v$VERSION"
echo "[Git] 准备推送的版本 Tag: $TAG_NAME"

# 4. 提交本地所有修改过的文件
echo "[Git] 正在暂存修改过的代码文件..."
git add .

# 自动识别当前所在分支
BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
if [ -z "$BRANCH" ]; then
    BRANCH="main"
fi

echo "[Git] 正在提交本地修改..."
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    git commit -m "bump: release version $TAG_NAME"
else
    echo "[Git] 未检测到文件变化，跳过 commit。"
fi

# 5. 推送代码和 Tag 触发云端编译
echo "[Git] 正在推送代码至 GitHub ($BRANCH 分支)..."
git push -u origin "$BRANCH"

# 如果本地已存在同名 Tag，先进行删除以防冲突
if git rev-parse "$TAG_NAME" >/dev/null 2>&1; then
    echo "[Git] 检测到本地已存在同名 Tag，正在删除重置..."
    git tag -d "$TAG_NAME"
fi

echo "[Git] 创建版本 Tag: $TAG_NAME..."
git tag "$TAG_NAME"

echo "[Git] 正在将 Tag 推送至 GitHub 触发 Actions 编译..."
git push origin -f "$TAG_NAME"

echo ""
echo "=========================================================="
echo "  🎉 代码推送及 Tag 发布全部完成！"
echo "  请访问您的 GitHub 仓库 Actions 标签页，查看 M 芯片版本编译进度。"
echo "=========================================================="
