"""pydxf — ultrafast loader for flattened ASCII DXF R11/R12 photonics layouts.

The heavy lifting is done by a small C++ core (``dxfcore/libdxfcore.dylib``) that
mmaps the file and parses it into Structure-of-Arrays buffers. This package wraps
those buffers as zero-copy numpy arrays and provides a queryable :class:`DxfLayout`.
"""

from .loader import DxfLayout, load

__all__ = ["DxfLayout", "load"]
