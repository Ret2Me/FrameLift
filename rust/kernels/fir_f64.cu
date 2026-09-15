// Each CUDA lane owns one output component. Do not parallel-reduce taps: that
// changes IEEE-754 addition order. Explicit rounded intrinsics also prohibit
// contraction into FMA regardless of later compiler defaults.
extern "C" __global__ void fir_f64(
    const double* input, const double* taps, double* output,
    unsigned long long input_start, unsigned long long input_samples,
    unsigned long long signal_samples, unsigned long long first_output_end,
    unsigned long long decimation, unsigned long long tap_count,
    unsigned long long output_samples, unsigned int channels) {
    const unsigned long long scalar = (unsigned long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (scalar >= output_samples * channels) return;
    const unsigned long long sample = scalar / channels;
    const unsigned long long component = scalar % channels;
    const unsigned long long end = first_output_end + sample * decimation;
    double sum = 0.0;
    for (unsigned long long j = 0; j < tap_count; ++j) {
        if (end >= j) {
            const unsigned long long k = end - j;
            if (k < signal_samples && k >= input_start && k - input_start < input_samples) {
                sum = __dadd_rn(sum, __dmul_rn(taps[j], input[(k - input_start) * channels + component]));
            }
        }
    }
    output[scalar] = sum;
}
