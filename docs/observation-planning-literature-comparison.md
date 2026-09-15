# Relation to Ronen and Ben-Moshe (2025)

Reference: Rony Ronen and Boaz Ben-Moshe, “Maximizing Nanosatellite
Throughput via Dynamic Scheduling and Distributed Ground Stations,” *Sensors*
25(24), 7538, DOI
[10.3390/s25247538](https://doi.org/10.3390/s25247538). Full open-access text is
also indexed by [PubMed Central](https://pmc.ncbi.nlm.nih.gov/articles/PMC12737175/).
Sources checked 2026-09-02.

## What the paper actually implements

The paper formulates NGSSP as a binary visibility/resource CSP whose utility is
the expected count of unique successfully decoded messages. Reception events
are Bernoulli variables with `P_decode(i,j,t)` obtained from aggregate ground
station statistics; distinct message receptions are assumed conditionally
independent. The authors explicitly describe this independence assumption as
an optimistic/theoretical upper-bound simplification when failures are
spatially correlated.

Its three implemented scheduling policies are heuristics:

- cooperative reception based on a Shapley-value marginal contribution;
- a local pair-utility rule
  `P_rx(station, pass) * product(1 - P_rx(neighbour, pass))` within 50 km;
- a weighted bidding/reward rule divided by the estimated number of competing
  visible stations.

Monte Carlo is used to approximate the otherwise exponential Shapley-value
calculation (`O(K * N_sat * N_visible)`). It is not presented as a calibrated
Monte Carlo forecast of a particular station's next reception. The paper does
not implement reinforcement learning. Edge AI/learnable policies are named as
future work. Its reported up-to-100% improvement is produced by simulation
against a greedy/round-robin-type baseline; the discussion calls real-hardware
validation and comparison with formal MILP a future milestone.

## What this implementation changes

This project separates estimation from allocation:

1. SGP4 and the applicable TLE create deterministic opportunity windows.
2. Frozen, leakage-resistant statistical models estimate reception
   probabilities and are evaluated on later real SatNOGS outcomes.
3. A centralized binary MILP selects the globally best set under receiver,
   satellite-contention, blocker and min/max mission constraints. HiGHS must
   prove optimality; an independent dynamic program verifies small synthetic
   interval instances.
4. A correlated Monte Carlo sensitivity layer applies shared station-day,
   satellite-day and environment-day shocks to the selected plan. Scenario
   parameters are not mislabeled as learned weather forecasts.
5. TLE, blocker, weather or history changes rebuild future opportunities and
   create an immutable plan revision only when policy says the change is
   material. Elapsed windows are never rescheduled.

For a bounded monthly plan with centralized knowledge, MILP directly answers
the global allocation question and gives an auditable optimality status. RL is
not automatically “better”: it would add approximation and policy-training
risk to a deterministic combinatorial problem already solved exactly at the
target scale. RL becomes a meaningful challenger only when decisions and state
arrive sequentially, the environment is validated, and it is tested on a new
untouched cohort against this MILP/probability baseline.

## Non-equivalence and claim boundary

The present SatNOGS replay treats one observation as one nominal sample because
Network does not expose a comparable sequence of message IDs. It therefore
does not reproduce the paper's exact message-level `delta_unique` objective or
its reported 100% gain. In the production planner, simultaneous duplicate
reception is conservatively suppressed by the target's
`exclusive_transmission` constraint, while mission-supplied
`nominal_unique_samples` expresses expected new content. A deployment that can
identify repeated onboard message IDs should add explicit content-key coverage
variables before claiming message-level good-put.

The historical study also cannot recover counterfactual passes that Network
never scheduled. A no-conflict replay is labeled non-informative, and the
synthetic MILP/Monte Carlo results remain engineering evidence. This is a more
limited claim than the paper's simulation claim, but it is traceable to real
labels and does not treat aggregate station success rates as time-varying truth.
