import base64
import os

from langchain_core.messages import HumanMessage

from utils.api_patch import build_audio_part


def _decode_pdf_data_uri(data_uri: str) -> bytes:
    """从 base64 data URI 解码出 PDF 字节。"""
    if "," in data_uri:
        data_uri = data_uri.split(",", 1)[1]
    return base64.b64decode(data_uri)


def _extract_pdf_text(data_uri: str) -> str:
    """从 base64 data URI 中提取 PDF 文本内容（纯文本模式）。"""
    try:
        import fitz  # pymupdf
        pdf_bytes = _decode_pdf_data_uri(data_uri)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if text.strip():
                pages.append(f"--- 第{i+1}页 ---\n{text.strip()}")
        doc.close()
        if not pages:
            return "(PDF 未提取到文本内容，可能是扫描件/纯图片 PDF)"
        return "\n\n".join(pages)
    except ImportError:
        return "(服务端未安装 pymupdf，无法解析 PDF。请运行: pip install pymupdf)"
    except Exception as e:
        return f"(PDF 解析失败: {str(e)})"


def _is_vision_model() -> bool:
    """根据 LLM_VISION_SUPPORT 环境变量或模型名自动判断是否支持视觉。

    :return: 是否支持视觉模型
    """
    explicit = os.getenv("LLM_VISION_SUPPORT", "").strip().lower()
    if explicit:
        return explicit == "true"

    model = os.getenv("LLM_MODEL", "").lower()
    vision_patterns = (
        "gpt-4o", "gpt-4-vision", "gpt-5", "gpt-o",
        "o1", "o3", "o4",
        "gemini",
        "claude",
        # Antigravity reverse-proxy may use original model names (gemini-*, claude-*)
        # which are already covered above; no extra patterns needed
    )
    for pattern in vision_patterns:
        if pattern in model:
            return True
    return False


def build_human_message(
    text: str,
    images: list[str] | None = None,
    files: list[dict] | None = None,
    audios: list[dict] | None = None,
) -> HumanMessage:
    """构造 HumanMessage，支持图片、文件附件（文本/PDF）和音频。

    :param text: 消息文本
    :param images: 图片 URL 列表
    :param files: 文件附件列表，每个文件包含 name、type、content
    :param audios: 音频附件列表
    :return: 构造好的 HumanMessage 对象
    """
    vision_supported = _is_vision_model()

    direct_file_parts: list[dict] = []

    # 媒体文件 MIME 类型映射
    media_mime = {
        ".avi": "video/x-msvideo", ".mp4": "video/mp4", ".mkv": "video/x-matroska",
        ".mov": "video/quicktime", ".webm": "video/webm",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
        ".ogg": "audio/ogg", ".aac": "audio/aac",
    }

    file_text = ""
    if files:
        file_parts = []
        for file_info in files:
            file_name = file_info.get("name", "未知文件")
            file_type = file_info.get("type", "text")
            file_content = file_info.get("content", "")

            if file_type == "pdf":
                if vision_supported:
                    pdf_text = _extract_pdf_text(file_content)
                    if len(pdf_text) > 50000:
                        pdf_text = pdf_text[:50000] + "\n\n... (文件过长，已截断)"
                    pdf_data_uri = file_content if file_content.startswith("data:") else f"data:application/pdf;base64,{file_content}"
                    direct_file_parts.append({
                        "type": "file",
                        "file": {
                            "filename": file_name,
                            "file_data": pdf_data_uri,
                        },
                    })
                    file_parts.append(f"📄 **附件: {file_name}** (已上传原始 PDF 供分析，同时附上提取的文本)\n```\n{pdf_text}\n```")
                else:
                    pdf_text = _extract_pdf_text(file_content)
                    if len(pdf_text) > 50000:
                        pdf_text = pdf_text[:50000] + "\n\n... (文件过长，已截断)"
                    file_parts.append(f"📄 **附件: {file_name}**\n```\n{pdf_text}\n```")
            elif file_type == "media":
                ext = os.path.splitext(file_name)[1].lower()
                mime = media_mime.get(ext, "application/octet-stream")
                data_uri = file_content if file_content.startswith("data:") else f"data:{mime};base64,{file_content}"
                direct_file_parts.append({
                    "type": "file",
                    "file": {
                        "filename": file_name,
                        "file_data": data_uri,
                    },
                })
                file_parts.append(f"🎬 **附件: {file_name}** (已上传原始媒体文件供分析)")
            else:
                if len(file_content) > 50000:
                    full_len = len(file_info.get("content", ""))
                    file_content = file_content[:50000] + f"\n\n... (文件过长，已截断，共 {full_len} 字符)"
                file_parts.append(f"📄 **附件: {file_name}**\n```\n{file_content}\n```")

        if file_parts:
            file_text = "\n\n" + "\n\n".join(file_parts)

    combined_text = (text or "") + file_text
    all_images = list(images or [])
    has_multimodal = bool(all_images) or bool(direct_file_parts) or bool(audios)

    if not has_multimodal:
        return HumanMessage(content=combined_text or "(空消息)")

    # 当前模型不支持多模态时的降级处理
    if not vision_supported and (all_images or audios):
        hints = []
        if all_images:
            hints.append(f"你发送了{len(images or [])}张图片，但当前模型不支持图片识别，图片已忽略。")
            all_images = []
        if audios:
            hints.append(f"你发送了{len(audios)}条语音，但当前模型不支持音频输入，语音已忽略。")
            audios = None
        combined_text += f"\n\n[系统提示：{'；'.join(hints)}请切换到支持多模态的模型（如 gemini-2.0-flash、gpt-4o）后重试。]"
        if not direct_file_parts:
            return HumanMessage(content=combined_text)

    content_parts = []
    if combined_text:
        content_parts.append({"type": "text", "text": combined_text})
    elif audios:
        content_parts.append({"type": "text", "text": "请听取并处理以下音频："})

    for image_data in all_images:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": image_data},
        })

    content_parts.extend(direct_file_parts)

    if audios:
        for audio in audios:
            audio_b64 = audio.get("base64", "")
            audio_fmt = audio.get("format", "webm")
            audio_name = audio.get("name", f"recording.{audio_fmt}")
            content_parts.append(build_audio_part(audio_b64, audio_fmt, audio_name))

    return HumanMessage(content=content_parts)
