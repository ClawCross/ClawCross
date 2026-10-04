"""Generate invitation QR codes locally; never send group tickets to a service."""
import base64
import qrcode


def invitation_qr(link: str) -> str:
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(link)
    code.make(fit=True)
    matrix = code.get_matrix()
    size = len(matrix)
    path = ''.join(f'M{x},{y}h1v1h-1z' for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size * 6}" height="{size * 6}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="white"/><path d="{path}" fill="black"/></svg>'
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
