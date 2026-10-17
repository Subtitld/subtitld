// Subtitld project thumbnails in Windows Explorer.
//
// A shell thumbnail provider (IThumbnailProvider, initialised with a stream)
// for .usfx files. A .usfx is a zip; Subtitld saves in it, uncompressed, a
// thumbnail.png: a card with the video's frame, its subtitle and the timeline
// where the project was left. This reads the zip's central directory from
// the stream Explorer hands over (Zip64 included), takes that entry, and
// decodes it with WIC at the size Explorer asks for. Files without one
// (saved before thumbnails) get no thumbnail: Explorer shows the type's icon.
//
// Registered by the installer (nsis-installer.nsi) and the MSIX package
// (packaging/msix/AppxManifest.xml); DllRegisterServer registers it for the
// current user, for testing. Builds with MSVC (CMakeLists.txt) or MinGW:
//   x86_64-w64-mingw32-g++ -std=c++17 -shared -O2 -static -DUNICODE -o UsfxThumbnail.dll
//       UsfxThumbnail.cpp UsfxThumbnail.def -lole32 -loleaut32 -lwindowscodecs -lshlwapi -luuid -lgdi32 -ladvapi32

#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <thumbcache.h>
#include <propsys.h>
#include <wincodec.h>
#include <cstdint>
#include <cstring>
#include <new>
#include <string>
#include <vector>

// {59A8C040-1202-4033-8DB5-F5E7A7709527}
static const CLSID CLSID_UsfxThumbnail =
    {0x59a8c040, 0x1202, 0x4033, {0x8d, 0xb5, 0xf5, 0xe7, 0xa7, 0x70, 0x95, 0x27}};
static const wchar_t CLSID_STRING[] = L"{59A8C040-1202-4033-8DB5-F5E7A7709527}";
static const wchar_t THUMBNAIL_HANDLER[] = L"{e357fccd-a995-4576-b01f-234630154e96}";

static HMODULE g_module = nullptr;
static LONG g_references = 0;

namespace {

const ULONGLONG MAX_THUMBNAIL = 8ull * 1024 * 1024;     // a thumbnail; not whatever a crafted file says
const char THUMBNAIL_NAME[] = "thumbnail.png";

uint16_t u16(const BYTE *p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
uint32_t u32(const BYTE *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | (static_cast<uint32_t>(p[3]) << 24); }
uint64_t u64(const BYTE *p) { return u32(p) | (static_cast<uint64_t>(u32(p + 4)) << 32); }

bool read_at(IStream *stream, ULONGLONG offset, void *buffer, ULONG size) {
    LARGE_INTEGER position;
    position.QuadPart = static_cast<LONGLONG>(offset);
    if (FAILED(stream->Seek(position, STREAM_SEEK_SET, nullptr)))
        return false;
    ULONG done = 0;
    BYTE *out = static_cast<BYTE *>(buffer);
    while (done < size) {
        ULONG got = 0;
        HRESULT hr = stream->Read(out + done, size - done, &got);
        if (FAILED(hr) || got == 0)
            return false;
        done += got;
    }
    return true;
}

// thumbnail.png's bytes, from the zip in `stream`; empty if it has none.
std::vector<BYTE> read_thumbnail(IStream *stream) {
    STATSTG stat = {};
    if (FAILED(stream->Stat(&stat, STATFLAG_NONAME)))
        return {};
    const ULONGLONG size = stat.cbSize.QuadPart;
    if (size < 22)
        return {};

    // The end of central directory record, in the last 64 KiB + 22 bytes.
    const ULONG tail_size = static_cast<ULONG>(size < 65557 ? size : 65557);
    std::vector<BYTE> tail(tail_size);
    const ULONGLONG tail_offset = size - tail_size;
    if (!read_at(stream, tail_offset, tail.data(), tail_size))
        return {};
    LONGLONG eocd = -1;
    for (LONGLONG i = static_cast<LONGLONG>(tail_size) - 22; i >= 0; --i) {
        if (u32(&tail[i]) == 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0)
        return {};
    ULONGLONG cd_offset = u32(&tail[eocd + 16]);
    ULONGLONG cd_size = u32(&tail[eocd + 12]);
    ULONGLONG entries = u16(&tail[eocd + 10]);

    // Zip64: the locator just before, pointing at the Zip64 record.
    if ((cd_offset == 0xFFFFFFFF || cd_size == 0xFFFFFFFF || entries == 0xFFFF) && eocd >= 20 &&
        u32(&tail[eocd - 20]) == 0x07064b50) {
        BYTE record[56];
        if (!read_at(stream, u64(&tail[eocd - 20 + 8]), record, sizeof(record)) || u32(record) != 0x06064b50)
            return {};
        entries = u64(record + 32);
        cd_size = u64(record + 40);
        cd_offset = u64(record + 48);
    }
    if (cd_offset + cd_size > size || cd_size > 64ull * 1024 * 1024)
        return {};

    std::vector<BYTE> directory(static_cast<size_t>(cd_size));
    if (cd_size && !read_at(stream, cd_offset, directory.data(), static_cast<ULONG>(cd_size)))
        return {};

    size_t p = 0;
    for (ULONGLONG n = 0; n < entries && p + 46 <= directory.size(); ++n) {
        const BYTE *entry = &directory[p];
        if (u32(entry) != 0x02014b50)
            return {};
        const uint16_t method = u16(entry + 10);
        ULONGLONG compressed = u32(entry + 20);
        ULONGLONG uncompressed = u32(entry + 24);
        const uint16_t name_length = u16(entry + 28), extra_length = u16(entry + 30), comment_length = u16(entry + 32);
        ULONGLONG local = u32(entry + 42);
        const size_t next = p + 46 + name_length + extra_length + comment_length;
        if (next > directory.size())
            return {};
        const std::string name(reinterpret_cast<const char *>(entry + 46), name_length);
        if (name == THUMBNAIL_NAME) {
            // Zip64 extra field: the sizes and offset that did not fit.
            const BYTE *extra = entry + 46 + name_length;
            for (size_t e = 0; e + 4 <= extra_length;) {
                const uint16_t id = u16(extra + e), length = u16(extra + e + 2);
                if (id == 0x0001) {
                    size_t f = e + 4;
                    if (uncompressed == 0xFFFFFFFF && f + 8 <= e + 4 + length) { uncompressed = u64(extra + f); f += 8; }
                    if (compressed == 0xFFFFFFFF && f + 8 <= e + 4 + length) { compressed = u64(extra + f); f += 8; }
                    if (local == 0xFFFFFFFF && f + 8 <= e + 4 + length) { local = u64(extra + f); }
                }
                e += 4 + length;
            }
            // Subtitld stores it as is; anything else is not its thumbnail.
            if (method != 0 || compressed != uncompressed || compressed == 0 || compressed > MAX_THUMBNAIL)
                return {};
            BYTE header[30];
            if (!read_at(stream, local, header, sizeof(header)) || u32(header) != 0x04034b50)
                return {};
            const ULONGLONG data_offset = local + 30 + u16(header + 26) + u16(header + 28);
            if (data_offset + compressed > size)
                return {};
            std::vector<BYTE> data(static_cast<size_t>(compressed));
            if (!read_at(stream, data_offset, data.data(), static_cast<ULONG>(compressed)))
                return {};
            static const BYTE png[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
            if (data.size() < 8 || memcmp(data.data(), png, 8) != 0)
                return {};
            return data;
        }
        p = next;
    }
    return {};
}

// The PNG, decoded and scaled to fit `cx` on its longer side, as a 32-bit DIB.
HRESULT decode(const std::vector<BYTE> &png, UINT cx, HBITMAP *bitmap) {
    IWICImagingFactory *factory = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory));
    if (FAILED(hr))
        return hr;
    IWICStream *wic_stream = nullptr;
    IWICBitmapDecoder *decoder = nullptr;
    IWICBitmapFrameDecode *frame = nullptr;
    IWICBitmapScaler *scaler = nullptr;
    IWICFormatConverter *converter = nullptr;
    UINT width = 0, height = 0;
    hr = factory->CreateStream(&wic_stream);
    if (SUCCEEDED(hr))
        hr = wic_stream->InitializeFromMemory(const_cast<BYTE *>(png.data()), static_cast<DWORD>(png.size()));
    if (SUCCEEDED(hr))
        hr = factory->CreateDecoderFromStream(wic_stream, nullptr, WICDecodeMetadataCacheOnDemand, &decoder);
    if (SUCCEEDED(hr))
        hr = decoder->GetFrame(0, &frame);
    if (SUCCEEDED(hr))
        hr = frame->GetSize(&width, &height);
    if (SUCCEEDED(hr) && (width == 0 || height == 0))
        hr = E_FAIL;
    UINT out_w = width, out_h = height;
    if (SUCCEEDED(hr) && (width > cx || height > cx)) {
        if (width >= height) { out_w = cx; out_h = height * cx / width; }
        else { out_h = cx; out_w = width * cx / height; }
        if (out_w == 0) out_w = 1;
        if (out_h == 0) out_h = 1;
    }
    if (SUCCEEDED(hr))
        hr = factory->CreateBitmapScaler(&scaler);
    if (SUCCEEDED(hr))
        hr = scaler->Initialize(frame, out_w, out_h, WICBitmapInterpolationModeFant);
    if (SUCCEEDED(hr))
        hr = factory->CreateFormatConverter(&converter);
    if (SUCCEEDED(hr))
        hr = converter->Initialize(scaler, GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone, nullptr, 0.0,
                                   WICBitmapPaletteTypeCustom);
    if (SUCCEEDED(hr)) {
        BITMAPINFO info = {};
        info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth = static_cast<LONG>(out_w);
        info.bmiHeader.biHeight = -static_cast<LONG>(out_h);       // top-down
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        void *bits = nullptr;
        HBITMAP dib = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
        if (!dib) {
            hr = E_OUTOFMEMORY;
        } else {
            hr = converter->CopyPixels(nullptr, out_w * 4, out_w * out_h * 4, static_cast<BYTE *>(bits));
            if (SUCCEEDED(hr))
                *bitmap = dib;
            else
                DeleteObject(dib);
        }
    }
    if (converter) converter->Release();
    if (scaler) scaler->Release();
    if (frame) frame->Release();
    if (decoder) decoder->Release();
    if (wic_stream) wic_stream->Release();
    factory->Release();
    return hr;
}

class UsfxThumbnailProvider : public IInitializeWithStream, public IThumbnailProvider {
public:
    UsfxThumbnailProvider() { InterlockedIncrement(&g_references); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void **ppv) override {
        static const QITAB table[] = {
            QITABENT(UsfxThumbnailProvider, IInitializeWithStream),
            QITABENT(UsfxThumbnailProvider, IThumbnailProvider),
            {nullptr, 0},
        };
        return QISearch(this, table, riid, ppv);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG count = InterlockedDecrement(&references_);
        if (count == 0)
            delete this;
        return count;
    }

    // IInitializeWithStream
    IFACEMETHODIMP Initialize(IStream *stream, DWORD) override {
        if (stream_)
            return HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED);
        if (!stream)
            return E_INVALIDARG;
        stream_ = stream;
        stream_->AddRef();
        return S_OK;
    }

    // IThumbnailProvider
    IFACEMETHODIMP GetThumbnail(UINT cx, HBITMAP *bitmap, WTS_ALPHATYPE *alpha) override {
        if (!bitmap || !alpha)
            return E_POINTER;
        *bitmap = nullptr;
        *alpha = WTSAT_ARGB;            // the card's corners are transparent
        if (!stream_)
            return E_UNEXPECTED;
        const std::vector<BYTE> png = read_thumbnail(stream_);
        if (png.empty())
            return E_FAIL;
        return decode(png, cx ? cx : 256, bitmap);
    }

private:
    virtual ~UsfxThumbnailProvider() {
        if (stream_)
            stream_->Release();
        InterlockedDecrement(&g_references);
    }
    LONG references_ = 1;
    IStream *stream_ = nullptr;
};

class ClassFactory : public IClassFactory {
public:
    IFACEMETHODIMP QueryInterface(REFIID riid, void **ppv) override {
        static const QITAB table[] = {QITABENT(ClassFactory, IClassFactory), {nullptr, 0}};
        return QISearch(this, table, riid, ppv);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return 2; }          // static
    IFACEMETHODIMP_(ULONG) Release() override { return 1; }
    IFACEMETHODIMP CreateInstance(IUnknown *outer, REFIID riid, void **ppv) override {
        *ppv = nullptr;
        if (outer)
            return CLASS_E_NOAGGREGATION;
        UsfxThumbnailProvider *provider = new (std::nothrow) UsfxThumbnailProvider();
        if (!provider)
            return E_OUTOFMEMORY;
        const HRESULT hr = provider->QueryInterface(riid, ppv);
        provider->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_references);
        else InterlockedDecrement(&g_references);
        return S_OK;
    }
};

ClassFactory g_factory;

LSTATUS set_value(HKEY root, const std::wstring &key, const wchar_t *name, const std::wstring &value) {
    HKEY handle = nullptr;
    LSTATUS status = RegCreateKeyExW(root, key.c_str(), 0, nullptr, 0, KEY_SET_VALUE, nullptr, &handle, nullptr);
    if (status != ERROR_SUCCESS)
        return status;
    status = RegSetValueExW(handle, name, 0, REG_SZ, reinterpret_cast<const BYTE *>(value.c_str()),
                            static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t)));
    RegCloseKey(handle);
    return status;
}

}  // namespace

extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}

extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID riid, void **ppv) {
    if (!IsEqualCLSID(clsid, CLSID_UsfxThumbnail)) {
        *ppv = nullptr;
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    return g_factory.QueryInterface(riid, ppv);
}

extern "C" HRESULT __stdcall DllCanUnloadNow() {
    return g_references == 0 ? S_OK : S_FALSE;
}

// For the current user (HKCU\Software\Classes), as regsvr32 does: what the
// installer writes, for testing a build without it.
extern "C" HRESULT __stdcall DllRegisterServer() {
    wchar_t path[MAX_PATH];
    if (!GetModuleFileNameW(g_module, path, MAX_PATH))
        return HRESULT_FROM_WIN32(GetLastError());
    const std::wstring clsid_key = std::wstring(L"Software\\Classes\\CLSID\\") + CLSID_STRING;
    LSTATUS status = set_value(HKEY_CURRENT_USER, clsid_key, nullptr, L"Subtitld project thumbnail");
    if (status == ERROR_SUCCESS)
        status = set_value(HKEY_CURRENT_USER, clsid_key + L"\\InprocServer32", nullptr, path);
    if (status == ERROR_SUCCESS)
        status = set_value(HKEY_CURRENT_USER, clsid_key + L"\\InprocServer32", L"ThreadingModel", L"Apartment");
    if (status == ERROR_SUCCESS)
        status = set_value(HKEY_CURRENT_USER, std::wstring(L"Software\\Classes\\.usfx\\ShellEx\\") + THUMBNAIL_HANDLER,
                           nullptr, CLSID_STRING);
    if (status != ERROR_SUCCESS)
        return HRESULT_FROM_WIN32(status);
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
    return S_OK;
}

extern "C" HRESULT __stdcall DllUnregisterServer() {
    RegDeleteTreeW(HKEY_CURRENT_USER, (std::wstring(L"Software\\Classes\\CLSID\\") + CLSID_STRING).c_str());
    RegDeleteTreeW(HKEY_CURRENT_USER, (std::wstring(L"Software\\Classes\\.usfx\\ShellEx\\") + THUMBNAIL_HANDLER).c_str());
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
    return S_OK;
}
