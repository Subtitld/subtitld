/* opus_encoder_ctl takes its value as a variadic argument, which ctypes can't pass rightly on Apple Silicon
 * (variadic arguments go on the stack there, not in registers): these two take a fixed one, for
 * src/subtitld/modules/opus.py. Linked into libopus itself by build-macos.sh. */
#include <opus.h>

OPUS_EXPORT int subtitld_opus_encoder_set(OpusEncoder *encoder, int request, opus_int32 value)
{
    return opus_encoder_ctl(encoder, request, value);
}

OPUS_EXPORT int subtitld_opus_encoder_get(OpusEncoder *encoder, int request, opus_int32 *value)
{
    return opus_encoder_ctl(encoder, request, value);
}
