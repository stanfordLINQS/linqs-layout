// dxf_parse.cpp
// Ultrafast parser for flattened ASCII DXF R11/R12 (AC1009) layouts.
//
// Design notes / assumptions (validated against layout.dxf, ~220 MB):
//   * The file is a strict sequence of (group-code line, value line) pairs.
//   * Group codes may be left-padded with spaces -> we trim before parsing.
//   * Only HEADER + ENTITIES sections; no BLOCKS/INSERT -> geometry is flat.
//   * Geometry is 2D: POLYLINE (closed polygons of straight segments) made of
//     VERTEX records terminated by SEQEND, plus CIRCLE (center + radius).
//     No bulge (42), no Z (30), no per-vertex width.
//
// Output is Structure-of-Arrays buffers handed to Python (zero-copy via ctypes):
//   verts      : interleaved [x0,y0, x1,y1, ...]            (2 * n_vertices)
//   poly_start : first vertex index of polyline i           (n_polylines)
//   poly_count : vertex count of polyline i                 (n_polylines)
//   poly_layer : layer id of polyline i                     (n_polylines)
//   poly_flags : DXF code-70 flags (bit0 = closed)          (n_polylines)
//   circ       : interleaved [x,y,r, ...]                   (3 * n_circles)
//   circ_layer : layer id of circle i                       (n_circles)
//   layer_names: interned, in id order
//
// Build:  see build.sh

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <unordered_map>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {

// Entity classes we track in the parse state machine.
enum Cur : uint8_t { NONE = 0, POLYLINE, VERTEX, CIRCLE, SEQEND, OTHER };

// Fast float parser for the fixed "[-]ddd.dddd" coordinate format produced by
// the layout tool. Falls back to strtod for anything exotic (exponents, etc.).
inline double parse_double(const char* s, const char* end) {
    while (s < end && (*s == ' ' || *s == '\t')) ++s;
    bool neg = false;
    if (s < end && (*s == '-' || *s == '+')) { neg = (*s == '-'); ++s; }
    const char* start = s;
    uint64_t ip = 0;
    while (s < end && *s >= '0' && *s <= '9') { ip = ip * 10 + (uint64_t)(*s - '0'); ++s; }
    double val = (double)ip;
    if (s < end && *s == '.') {
        ++s;
        uint64_t frac = 0;
        double scale = 1.0;
        while (s < end && *s >= '0' && *s <= '9') {
            frac = frac * 10 + (uint64_t)(*s - '0');
            scale *= 10.0;
            ++s;
        }
        val += (double)frac / scale;
    }
    // Unexpected character (exponent, etc.) -> robust fallback.
    if (s < end && (*s == 'e' || *s == 'E')) {
        return strtod(start - (neg ? 1 : 0), nullptr);
    }
    return neg ? -val : val;
}

inline long parse_long(const char* s, const char* end) {
    while (s < end && (*s == ' ' || *s == '\t')) ++s;
    bool neg = false;
    if (s < end && (*s == '-' || *s == '+')) { neg = (*s == '-'); ++s; }
    long v = 0;
    while (s < end && *s >= '0' && *s <= '9') { v = v * 10 + (*s - '0'); ++s; }
    return neg ? -v : v;
}

// Classify a code-0 keyword value. [s,e) is already newline-bounded.
inline Cur classify(const char* s, const char* e) {
    while (s < e && (*s == ' ' || *s == '\t')) ++s;
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) --e;
    size_t n = (size_t)(e - s);
    if (n == 0) return OTHER;
    switch (s[0]) {
        case 'P': return POLYLINE;                       // POLYLINE
        case 'V': return VERTEX;                         // VERTEX
        case 'C': return CIRCLE;                         // CIRCLE
        case 'S': return (n >= 3 && s[2] == 'Q') ? SEQEND : OTHER; // SEQEND vs SECTION
        default:  return OTHER;                          // ENDSEC / EOF / etc.
    }
}

} // namespace

struct DxfDoc {
    std::vector<double>  verts;       // interleaved x,y
    std::vector<int64_t> poly_start;
    std::vector<int32_t> poly_count;
    std::vector<int32_t> poly_layer;
    std::vector<uint8_t> poly_flags;
    std::vector<double>  circ;        // interleaved x,y,r
    std::vector<int32_t> circ_layer;
    std::vector<std::string> layer_names;
    std::unordered_map<std::string, int32_t> layer_ids;

    int32_t intern(const char* s, const char* e) {
        while (s < e && (*s == ' ' || *s == '\t')) ++s;
        while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) --e;
        std::string key(s, (size_t)(e - s));
        auto it = layer_ids.find(key);
        if (it != layer_ids.end()) return it->second;
        int32_t id = (int32_t)layer_names.size();
        layer_names.push_back(key);
        layer_ids.emplace(std::move(key), id);
        return id;
    }
};

extern "C" {

DxfDoc* dxf_load(const char* path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return nullptr;
    struct stat st;
    if (fstat(fd, &st) != 0) { close(fd); return nullptr; }
    size_t size = (size_t)st.st_size;
    if (size == 0) { close(fd); return nullptr; }

    const char* data = (const char*)mmap(nullptr, size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (data == MAP_FAILED) return nullptr;
    madvise((void*)data, size, MADV_SEQUENTIAL);

    DxfDoc* doc = new DxfDoc();
    // Heuristic reserves to avoid reallocations on big files.
    doc->verts.reserve(size / 16);
    doc->poly_start.reserve(size / 1024);
    doc->poly_count.reserve(size / 1024);
    doc->poly_layer.reserve(size / 1024);
    doc->poly_flags.reserve(size / 1024);
    doc->circ.reserve(size / 1024);
    doc->circ_layer.reserve(size / 3072);

    const char* p = data;
    const char* end = data + size;

    Cur cur = NONE;
    int64_t cur_poly = -1;          // index into poly_* of the open polyline
    double tx = 0, ty = 0, tr = 0;  // pending vertex/circle coords
    int32_t tlayer = -1;            // pending circle layer

    // Flush the entity that just ended (its fields are fully read).
    auto finalize = [&]() {
        switch (cur) {
            case VERTEX:
                doc->verts.push_back(tx);
                doc->verts.push_back(ty);
                break;
            case CIRCLE:
                doc->circ.push_back(tx);
                doc->circ.push_back(ty);
                doc->circ.push_back(tr);
                doc->circ_layer.push_back(tlayer);
                break;
            default:
                break;
        }
    };

    while (p < end) {
        // --- CODE line ---
        while (p < end && (*p == ' ' || *p == '\t')) ++p;
        bool neg = false;
        if (p < end && *p == '-') { neg = true; ++p; }
        int code = 0;
        while (p < end && *p >= '0' && *p <= '9') { code = code * 10 + (*p - '0'); ++p; }
        if (neg) code = -code;
        const char* nl = (const char*)memchr(p, '\n', (size_t)(end - p));
        if (!nl) break;
        p = nl + 1;

        // --- VALUE line ---
        const char* vstart = p;
        nl = (const char*)memchr(p, '\n', (size_t)(end - p));
        const char* vend = nl ? nl : end;
        p = nl ? nl + 1 : end;

        switch (code) {
            case 0: {
                finalize();
                cur = classify(vstart, vend);
                if (cur == POLYLINE) {
                    cur_poly = (int64_t)doc->poly_start.size();
                    doc->poly_start.push_back((int64_t)(doc->verts.size() / 2));
                    doc->poly_count.push_back(0);
                    doc->poly_layer.push_back(-1);
                    doc->poly_flags.push_back(0);
                } else if (cur == VERTEX) {
                    tx = ty = 0;
                } else if (cur == CIRCLE) {
                    tx = ty = tr = 0;
                    tlayer = -1;
                } else if (cur == SEQEND) {
                    if (cur_poly >= 0) {
                        int64_t start = doc->poly_start[cur_poly];
                        int64_t n = (int64_t)(doc->verts.size() / 2) - start;
                        doc->poly_count[cur_poly] = (int32_t)n;
                        cur_poly = -1;
                    }
                }
                break;
            }
            case 8: {
                int32_t id = doc->intern(vstart, vend);
                if (cur == POLYLINE && cur_poly >= 0) doc->poly_layer[cur_poly] = id;
                else if (cur == CIRCLE) tlayer = id;
                break;
            }
            case 70: {
                if (cur == POLYLINE && cur_poly >= 0)
                    doc->poly_flags[cur_poly] = (uint8_t)parse_long(vstart, vend);
                break;
            }
            case 10: {
                double x = parse_double(vstart, vend);
                if (cur == VERTEX || cur == CIRCLE) tx = x;
                break;
            }
            case 20: {
                double y = parse_double(vstart, vend);
                if (cur == VERTEX || cur == CIRCLE) ty = y;
                break;
            }
            case 40: {
                if (cur == CIRCLE) tr = parse_double(vstart, vend);
                break;
            }
            default:
                break;
        }
    }
    finalize(); // flush a trailing pending entity (defensive)

    munmap((void*)data, size);
    return doc;
}

void           dxf_free(DxfDoc* d)            { delete d; }
int64_t        dxf_num_polylines(DxfDoc* d)   { return (int64_t)d->poly_start.size(); }
int64_t        dxf_num_vertices(DxfDoc* d)    { return (int64_t)(d->verts.size() / 2); }
int64_t        dxf_num_circles(DxfDoc* d)     { return (int64_t)d->circ_layer.size(); }
int64_t        dxf_num_layers(DxfDoc* d)      { return (int64_t)d->layer_names.size(); }
const double*  dxf_verts(DxfDoc* d)           { return d->verts.data(); }
const int64_t* dxf_poly_start(DxfDoc* d)      { return d->poly_start.data(); }
const int32_t* dxf_poly_count(DxfDoc* d)      { return d->poly_count.data(); }
const int32_t* dxf_poly_layer(DxfDoc* d)      { return d->poly_layer.data(); }
const uint8_t* dxf_poly_flags(DxfDoc* d)      { return d->poly_flags.data(); }
const double*  dxf_circ(DxfDoc* d)            { return d->circ.data(); }
const int32_t* dxf_circ_layer(DxfDoc* d)      { return d->circ_layer.data(); }
const char*    dxf_layer_name(DxfDoc* d, int64_t i) {
    if (i < 0 || i >= (int64_t)d->layer_names.size()) return "";
    return d->layer_names[(size_t)i].c_str();
}

} // extern "C"
