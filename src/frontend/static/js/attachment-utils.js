/* Shared, bounded image preparation for Studio and the message center. */
(function (root) {
    const MAX_GROUP_BYTES = 512 * 1024;
    function groupPayloadBytes(content, attachments = []) {
        // Conservative allowance for JSON spaces added by the server serializer.
        return new TextEncoder().encode(JSON.stringify({content, attachments})).length + 4096;
    }
    function assertGroupSize(content, attachments = []) {
        if (groupPayloadBytes(content, attachments) > MAX_GROUP_BYTES) {
            throw new Error('群消息和附件合计最多 512 KiB / Group message and attachments exceed 512 KiB. 请减少附件或改用文件链接。');
        }
    }
    async function prepareImage(file, {maxBytes = 512 * 1024, maxSide = 1536} = {}) {
        if (file.size > 50 * 1024 * 1024) throw new Error('图片超过 50 MiB / Image exceeds 50 MiB');
        const url = URL.createObjectURL(file);
        const image = new Image();
        try {
            await new Promise((resolve, reject) => {
                image.onload = resolve;
                image.onerror = () => reject(new Error('无法读取图片，请转换成 JPEG/PNG 后重试 / Cannot decode image; convert HEIC or other formats to JPEG/PNG.'));
                image.src = url;
            });
            if (!image.width || !image.height) throw new Error('图片尺寸无效 / Invalid image dimensions');
            const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
            const canvas = document.createElement('canvas');
            canvas.width = Math.max(1, Math.round(image.width * scale));
            canvas.height = Math.max(1, Math.round(image.height * scale));
            const context = canvas.getContext('2d');
            if (!context) throw new Error('浏览器无法处理图片 / Image processing unavailable');
            for (let pass = 0; pass < 8; pass++) {
                context.fillStyle = '#fff';
                context.fillRect(0, 0, canvas.width, canvas.height);
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                for (const quality of [0.85, 0.7, 0.55, 0.4]) {
                    const result = canvas.toDataURL('image/jpeg', quality);
                    if (Math.ceil((result.length - result.indexOf(',') - 1) * 3 / 4) <= maxBytes) return result;
                }
                canvas.width = Math.max(1, Math.floor(canvas.width * 0.75));
                canvas.height = Math.max(1, Math.floor(canvas.height * 0.75));
            }
            throw new Error('图片压缩后仍过大 / Image is still too large after compression');
        } finally { URL.revokeObjectURL(url); }
    }
    root.ClawCrossAttachments = {prepareImage, assertGroupSize, groupPayloadBytes};
})(typeof window !== 'undefined' ? window : globalThis);
