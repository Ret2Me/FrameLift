# RML24 IQ-only feature AMR baseline v1

Date: 2026-09-02

## Verdict

The frozen validation rule selected `ridge_linear_classifier_lambda_10.0`. On the declared
row-level holdout it achieved top-1 accuracy 0.354119,
top-3 accuracy 0.554044, and macro recall
0.354119 across all 21 released classes.

This is an IQ-only modulation-routing result. It is not signal detection,
bit demodulation, validated packet yield, or evidence about real satellite IQ.

## Frozen model selection

| Model | Validation top-1 | Validation top-3 |
|---|---:|---:|
| `nearest_standardized_class_centroid` | 0.150038 | 0.344671 |
| `ridge_linear_classifier_lambda_0.01` | 0.333333 | 0.555178 |
| `ridge_linear_classifier_lambda_0.1` | 0.333333 | 0.555178 |
| `ridge_linear_classifier_lambda_1.0` | 0.334467 | 0.554800 |
| `ridge_linear_classifier_lambda_10.0` | 0.340892 | 0.555556 |

## Holdout by class

| Class | Recall |
|---|---:|
| 16APSK | 0.198413 |
| 16QAM | 0.015873 |
| 32APSK | 0.246032 |
| 32QAM | 0.031746 |
| 64QAM | 0.206349 |
| 8PSK | 0.365079 |
| ARTM | 0.523810 |
| BPSK | 0.523810 |
| BPSK_FM | 0.174603 |
| BPSK_PM | 0.119048 |
| FM | 0.412698 |
| FQPSK | 0.380952 |
| FQPSK_PM | 0.738095 |
| FSK_PM | 0.341270 |
| GMSK | 0.460317 |
| OQPSK | 0.515873 |
| PM | 0.484127 |
| QPSK | 0.325397 |
| QPSK_FM | 0.396825 |
| QPSK_PM | 0.444444 |
| SOQPSK | 0.531746 |

## Claim boundary

This is a post-development modulation-routing diagnostic on RML24, not a pristine confirmation, signal-presence detection, bit recovery, frame yield, or performance on real satellite IQ.
