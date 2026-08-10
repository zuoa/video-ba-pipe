/**
 * 复制文本到剪贴板，兼容非安全上下文（如局域网 HTTP 访问）。
 *
 * navigator.clipboard 仅在安全上下文（HTTPS 或 localhost）下可用；
 * Docker 部署经局域网 IP 以 HTTP 访问时该 API 不存在，writeText 会抛错，
 * 因此需要回退到隐藏 textarea + document.execCommand('copy') 的方案。
 *
 * @returns 是否复制成功
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;

  if (navigator?.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限被拒或其它异常，落到下面的兜底方案
    }
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    // 放到视口外并隐藏，避免页面出现跳动或触发屏幕阅读器
    textarea.style.position = 'fixed';
    textarea.style.top = '0';
    textarea.style.left = '0';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}
