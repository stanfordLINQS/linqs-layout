"""viewer — a lightning-fast GPU layout viewer for flattened DXF layouts.

Built on the zero-copy SoA arrays from :mod:`pydxf`. All geometry is uploaded to
the GPU once as a single ``GL_LINES`` batch (polyline outlines) plus an instanced
circle pass; per-layer color and visibility are resolved in the vertex shader, so
toggling a layer is a one-float uniform write with no buffer rebuild.

- :mod:`viewer.scene`     context-agnostic moderngl renderer
- :mod:`viewer.camera`    orthographic pan / zoom-at-cursor camera
- :mod:`viewer.palette`   distinct per-layer colors
- :mod:`viewer.snap`      measuring-tool snapping (nearest corner / edge)
- :mod:`viewer.style`     design tokens + app-wide stylesheet
- :mod:`viewer.offscreen` headless render-to-PNG (works without a display)
- :mod:`viewer.overlay`   measurement / scale-bar HUD
- :mod:`viewer.viewport`  the GL viewport widget (pan / zoom / measure)
- :mod:`viewer.panel`     the layer-panel sidebar
- :mod:`viewer.window`    tabbed window, per-tab view, welcome screen
- :mod:`viewer.app`       application entry / file opening
"""

from .camera import Camera2D
from .scene import GLScene

__version__ = "1.0.13"       # single source of truth (read by the spec + update check)

__all__ = ["GLScene", "Camera2D", "__version__"]
