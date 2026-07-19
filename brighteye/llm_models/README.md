# 宸观 BrightEye · 大模型获取（llm_models/）

> 本项目全部 AI 能力（桌宠对话 / 复盘洞察 / 动态台词）默认走**本地 Ollama**，
> 模型文件共约 **8.8GB**，超过 GitHub 单文件 100MB / LFS 免费 1GB / Release
> 附件 2GB 的限制，因此**仓库不含模型本体**，改为本文件夹的一键安装脚本分发。
> 未安装模型也能完整运行（离线优先铁律：聊天走离线脚本、复盘走规则建议）。

## 所需模型

| 模型 | 用途 | 体积 | 说明 |
| --- | --- | --- | --- |
| `qwen2.5:7b-instruct` | 桌宠聊天 / 动态台词 | ≈4.7GB | 指令跟随与角色扮演自然 |
| `deepseek-r1:7b` | 复盘 / 周报月报 AI 洞察 | ≈4.7GB | 推理型，思维链自动剥离 |

## 一键安装

```bash
# Windows：双击运行（自动装 Ollama + 拉取两个模型）
llm_models\install_models.bat

# Linux / macOS
bash llm_models/install_models.sh
```

脚本流程：检测 Ollama →（缺失则 winget/官方脚本安装）→ `ollama pull` 两个模型。
安装完成后**无需任何配置**，软件启动会自动拉起 Ollama 服务并接入（v1.12.0）。

## 低配机替代（可选）

显存/内存不足时可换小模型（质量略降）：

```bash
ollama pull qwen2.5:1.5b        # ≈1GB
```

然后修改 `brighteye/config.py` 中 `LLMConfig.chat_model = "qwen2.5:1.5b"`
（`analysis_model` 同理，或留空跳过 AI 洞察走规则版）。

## 云端 API 替代（可选）

不装本地模型也可设环境变量走 OpenAI 兼容 API（如 DeepSeek 官方）：

```
BRIGHTEYE_LLM_BASE = https://api.deepseek.com/v1
BRIGHTEYE_LLM_KEY  = sk-xxxx
```

详见 `docs/大模型接入与部署指南.md`。
