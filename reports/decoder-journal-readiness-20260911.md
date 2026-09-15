# Decoder publication readiness and journal route

Assessment date: 2026-09-11 UTC. This is a publication planning memorandum, not a submitted manuscript, acceptance prediction, or confirmation that the decoder is publication-ready. No journal account, submission, payment, external upload, or author declaration was made.

## Decision

**Do not yet describe the present demodulator as a demonstrated advance or run a purported final confirmatory experiment with an unresolved positive-control failure.** The research status supplied for this assessment is an unfinished private-OGG campaign, zero confirmed native AX.25 UI frames so far, and external decoder candidates requiring protocol-specific validation. That status does not establish that every recording contains no telemetry, and it does not establish that our decoder is inferior on identical IQ: those are different claims requiring different evidence. Older public development results must not silently become independent confirmation.

Conditional recommendation:

| Evidence achieved after diagnosis | Recommended route | Claim that would be supportable |
|---|---|---|
| Reusable, reproducible, validated software, with useful recovery or workflow benefits, but no clearly novel demodulation result | **SoftwareX**, original software publication, first candidate | A research-software contribution with a precisely bounded application and measured behavior |
| A technically distinct receiver component with independently confirmed improvement on held-out real captures, ablations, false-positive controls and resource measurements | **Radio Science**, original research paper | A new and significant satellite-reception method, with its actual scope and limitations |
| Mature open research software with sustained public development and demonstrable use | **JOSS**, later software-paper option | Research software and its design, usability and impact, rather than a new-results paper |
| Remaining unexplained control failures or only configuration coverage / unvalidated candidates | **No journal-ready performance manuscript yet** | Internal diagnostic report, reproducible benchmark protocol and carefully labeled preliminary findings |

This ranking is a reasoned fit assessment, not an acceptance probability. The checked official sources do not establish our manuscript's chance of acceptance. A validated software contribution is a more defensible near-term target than a claim of a revolutionary demodulator. A careful negative-result or archival-quality study might eventually be publishable, but unresolved implementation or measurement errors are not themselves a scientific negative result.

## Verified venue requirements

### SoftwareX: provisional first target for a software contribution

Elsevier describes SoftwareX as a multidisciplinary open-access, peer-reviewed venue for research software and its applications, with a dedicated submission template. That supports a reusable telemetry-recovery tool as a plausible subject; it does not guarantee that a thin decoder wrapper or unvalidated implementation is sufficient. [Official Elsevier Research Elements description](https://www.elsevier.com/researcher/author/tools-and-resources/research-elements-journals).

The official SoftwareX review form assesses an original software publication's research usefulness, empirical evidence, reproducibility, installation, operating environment, documentation, automated tests, maintainability and appropriate open-source licensing. Accordingly, the package should include a clean build, executable examples, verified captures, full commands and a regression suite; the paper should explain what research users can actually do with it. [Official SoftwareX reviewer form](https://legacyfileshare.elsevier.com/promis_misc/softwarex-reviewer-form.pdf).

Verification limitation: the current [journal Guide for Authors](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors) and [aims and scope page](https://www.sciencedirect.com/journal/softwarex/about/aims-and-scope) returned HTTP 403 during this check; the official linked template could not be extracted through the browsing tool. Therefore **current exact word/figure limits, detailed repository requirements, journal-specific data-sharing policy and APC amount are not certified here**. They must be rechecked against the accessible current official guide before preparing a submission package. Conflicting third-party word limits and historical APC quotations were deliberately not adopted.

Elsevier permits disclosed AI assistance in manuscript preparation with human review and responsibility; AI use within the research belongs in Methods. The final manuscript needs both an accurate research-method account of AI-assisted development/analysis and the publisher's manuscript-preparation disclosure, where applicable. An AI cannot be an author. Do not assert that human review has occurred before it actually has. [Current Elsevier generative-AI policy](https://www.elsevier.com/about/policies-and-standards/generative-ai-policies-for-journals).

### Radio Science: method paper if a real advance is established

The journal explicitly includes satellite communication and terrestrial/space-based signals and systems. It requires new and significant technical content, complete experimental data, and adequate descriptions of apparatus, methods and experimental conditions. A protocol-aware receiver improvement with controlled real-capture evidence is within that stated scope; a parameter-bank sweep alone is not automatically a new algorithm. [Official Radio Science aims and scope](https://agupubs.onlinelibrary.wiley.com/hub/journal/1944799X/aims-and-scope/read-full-aims-and-scope).

AGU requires the supporting data and central software to be accessible for peer review, with archival preservation, appropriate citations and an Open Research availability statement. A moving GitHub branch alone is not the preserved research release. Access restrictions must be explained rather than replaced by a vague promise to supply files on request. [AGU data/software sharing guidance](https://data.agu.org/resources/agu-data-software-sharing-guidance).

AGU requires transparent disclosure of AI used in writing or research, identifying what was used and how; human authors remain accountable. Research articles must provide a developed analysis, results and discussion, not only a proposal. [AGU text and authorship requirements](https://www.agu.org/publications/authors/journals/text-graphics-requirements), [AGU article types](https://www.agu.org/Publications/Authors/Journals).

Official listed charges at the check date: Radio Science base publication fee **US$1,000** on the non-open-access route; excess-length fee **US$125 per publication unit**; optional open-access APC **US$3,700**, replacing base/excess fees. Funding eligibility, taxes and waivers require the actual authors' circumstances; no fee waiver or coverage is presumed. These are listed prices, not a quote or spending authorization. [AGU publication-fee table](https://www.agu.org/publications/authors/journals/publication-fees).

### JOSS: worthwhile later, not an immediate fallback

JOSS requires open-source research software, accessible source/issues, documentation, tests, feature completeness, demonstrated research use and **more than six months of active public development history**. Publishing a previously private repository immediately before submission does not meet that gate. It evaluates software rather than a paper centered on new scientific results. Publication and submission are free. Its current policy requires a detailed AI-use disclosure and human responsibility; AI assistance is not allowed for author/editor/reviewer conversational exchanges except translation. A final reviewed release is archived with a DOI. Unless the required public development record already exists and is verified, do not schedule an immediate JOSS submission. [Official JOSS submission and AI policies](https://joss.readthedocs.io/en/latest/submitting.html).

## Empirical gates before a final benchmark

The following are proposed project criteria, not numerical thresholds mandated by a journal. They should be frozen before the confirmatory run, with any later amendment labeled and justified.

1. **Positive-control identity.** For CANVAS #5122 and other successful station observations, bind the saved PDU and recording to the same instance, observation and time interval. Verify the PDU using its actual protocol. Determine whether the original successful path used IQ, preprocessed samples or the archived OGG. A valid live-IQ PDU is not proof that a lossy archived audio file retains the necessary information.
2. **Representation-specific receiver correctness.** Establish end-to-end recovery on known clean and impaired fixtures for every claimed modulation/framing combination. Include independent reference vectors or captures, not only self-generated encoder/decoder round trips. Test symbol rate, frequency offset/drift, deviation, polarity, scrambling, bit order, timing, rate conversion and FCS/CRC boundaries. Separate OGG/real-audio contracts from complex-IQ contracts.
3. **Protocol-aware evidence.** Separate native CRC-reverified frames, backend-attested CRC/FEC frames, syntactically plausible packets and raw candidates. AX.25 UI is a specific category, not the definition of all telemetry. Define validation and deduplication separately for supported CCSDS, AX.25 variants, CSP or other formats; retain unknowns as unknowns.
4. **Fair independent arms.** Identify our Rust receiver, our experimental receiver, actual native SatNOGS, Dire Wolf and gr-satellites unambiguously. Give eligible arms the exact same source samples and declared transforms; preserve software/configuration versions. A missing gr-satellites satellite definition must not suppress another decoder that has sufficient parameters. Unsupported, failed, timed-out and completed-empty are distinct outcomes.
5. **No development leakage.** Captures, settings and satellites already inspected while tuning remain development or explicitly retrospective evaluation data. Freeze a fresh held-out selection by time/satellite/station groups as appropriate. Select it without looking at recovered yield. Report missing URLs, exclusions, unsupported cases and all valid eligible observations; do not keep only improvement cases.
6. **Predeclare outcomes and uncertainty.** Primary outcome: per-observation unique verified frame identities and recovered bytes, with gained and lost frames relative to each baseline and the baseline union. Report global uniqueness separately because repeated beacon payloads and retransmissions can otherwise distort yield. Confidence intervals should preserve dependence within observations/passes and repeated satellites; a packet is not automatically an independent replicate. Use pilot variability for sample-size planning, not a magical fixed recording count.
7. **Mechanistic evidence and negatives.** Ablate each proposed new component under matched inputs and budgets. Include noise-only, no-transmission and structurally invalid controls matched to the complete search procedure. Searching many parameter hypotheses increases opportunities for false acceptance; account for all searches when reporting the negative-control exposure. Distinguish CRC-guided search from independent proof of a real packet. If no independent truth exists, label the evidence level explicitly.
8. **Resource and replay reproducibility.** Report wall time, CPU time, peak RAM, thread count, hardware, cold/warm-start conditions and all timeout rules. Verify resumed/deep mode preserves prior verified output and cannot silently reuse another input/configuration's cache. A release must reproduce the result tables from immutable manifests, logs and machine-readable records on a clean environment.

A useful readiness decision is narrower than “supports all popular protocols”: it names exactly which modulation, framing and input combinations passed these gates. A first sound publication can deliberately cover a smaller domain. Wider coverage without validated end-to-end controls would weaken the claim.

## Suggested paper structures after the gates pass

### SoftwareX candidate

Working title, not a result claim: **“A reproducible, resumable toolkit for retrospective satellite telemetry recovery.”**

1. Motivation and significance: expensive observations already exist; reproducible reprocessing and per-frame evidence are the research need. Explain how the tool complements established receivers rather than conflating SatNOGS with a single decoder.
2. Software description: input contracts, independently eligible backends, validated protocol adapters, frozen profiles, deterministic orchestration, resource-budget modes, checkpoint identities and provenance records. State which pieces are reused versus developed here.
3. Illustrative examples: one successful audio example, one IQ example only if actually supported and tested, and one nonrecoverable/unsupported case with an informative reason. Supply exact replay commands and expected hashes.
4. Evaluation: held-out paired comparisons, protocol-specific evidence levels, real-data gains/losses, resource curves, ablations and negative controls. Put unsuccessful cases in the main accounting.
5. Impact, limits and availability: observed use, supported scope, archival-input limitations, maintainability and frozen data/software access. Include measured operational benefits even if sensitivity superiority is absent, without pretending they constitute new DSP.

Use the current official template once obtained. Do not invent authors, affiliations, funding, a DOI, an open-source release or adoption. Code-release and human author-review statements remain pending until true.

### Radio Science candidate

Working title: **“[Verified receiver component] for satellite telemetry recovery from [validated input class].”** The brackets must be replaced by demonstrated components and scope, not promotional adjectives.

1. Introduction and closely matched prior art, stating the specific unresolved receiver problem.
2. Signal and channel model, receiver algorithm, assumptions, complexity and a clear difference from established synchronization/sequence-decoding/search methods.
3. Experimental design: independent real captures, representational controls, exact baselines, frozen settings, frame validation and statistical plan.
4. Results: primary paired gain/loss and byte yield; generalization across held-out groups; ablation and impairment analyses; false-positive exposure; resource tradeoffs.
5. Discussion: failure modes, what an archival OGG cannot establish about original IQ, operational implications and limits of generalization.
6. Conclusions containing only demonstrated claims; Open Research and AI-use disclosures.

## Immediate handoff

Finish the current campaign without rewriting its frozen artifacts. Treat it as a diagnostic/development cohort unless its independence from all tuning can genuinely be demonstrated. Resolve the positive-control and protocol-validation issues before selecting the final claim. Then choose the conditional route above and draft a results-bearing manuscript from audited tables. Until that point, a manuscript skeleton is appropriate; a finished “revolutionary method” paper is not.

This memorandum uses only official publisher/journal sources for venue claims. The empirical readiness gates are an explicit project recommendation derived from the supplied research status, not a completed audit of the source tree or a fresh measurement of running jobs.
