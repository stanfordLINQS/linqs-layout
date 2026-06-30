"""viewer — a lightning-fast GPU layout viewer for flattened DXF photonics layouts.

Built on the zero-copy SoA arrays from :mod:`pydxf`. All geometry is uploaded to
the GPU once as a single ``GL_LINES`` batch (polyline outlines) plus an instanced
circle pass; per-layer color and visibility are resolved in the vertex shader, so
toggling a layer is a one-float uniform write with no buffer rebuild.

- :mod:`viewer.scene`     context-agnostic moderngl renderer
- :mod:`viewer.camera`    orthographic pan / zoom-at-cursor camera
- :mod:`viewer.palette`   distinct per-layer colors
- :mod:`viewer.offscreen` headless render-to-PNG (works without a display)
- :mod:`viewer.qt_app`    interactive PySide6 window with a layer sidebar
"""

from .camera import Camera2D
from .scene import GLScene

__all__ = ["GLScene", "Camera2D"]
