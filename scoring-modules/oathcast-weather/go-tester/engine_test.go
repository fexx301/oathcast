package main

import (
	"fmt"
	"sort"
	"testing"
)

// A WASM module is supposed to be the one artefact whose behaviour does not
// depend on the machine underneath it. Registration leans on that: Telegraph
// pins the module by Keccak over its raw bytes, and every margin quoted in
// release-evidence.json is only evidence if those bytes score the same way on
// the validator as they do here.
//
// They do not, in one measured case. Scoring the ranking pools produced a worst
// separation of -0.4925 on darwin/arm64 and -0.3725 in CI on linux/amd64, from
// the same commit and the same pinned rustc 1.95.0. Ruling out the obvious
// causes took four steps, and the order matters because the first two answers
// were both wrong:
//
//	The two builds differ.        True but irrelevant. darwin/arm64 and
//	                              linux/amd64 produce different bytes (2c1f7ad3
//	                              vs 1daaf068, both 42,790 bytes, both matching
//	                              the recorded platform digests). Running the
//	                              linux bytes on the darwin host reproduced the
//	                              darwin scores exactly, so the bytes do not
//	                              carry the difference.
//	Our Rust is non-deterministic. No. The divergence reproduces on a single
//	                              call into a freshly instantiated module, so it
//	                              cannot come from allocator history or any
//	                              other cross-call state.
//	The host arch decides.        No. linux/arm64 agrees with darwin/arm64, and
//	                              linux/amd64 under the interpreter agrees with
//	                              both.
//	The engine decides.           Yes. Of six os/arch/engine combinations, five
//	                              agree; the outlier is wazero v1.12.0's amd64
//	                              compiler backend. GitHub's native amd64
//	                              runners reproduce it, so it is the backend and
//	                              not local emulation.
//
// The tempting fix was to lower the ranking floors until CI went green. That
// would have recorded a runtime miscompilation as a property of our scorer.
// Instead the ranking benchmark runs on the interpreter, where all six
// combinations agree, and this test pins the divergence itself so a new one
// fails loudly instead of being absorbed into a floor.
//
// The allowlist is a ceiling, not an expectation. It holds on arm64, where
// nothing diverges, and it will keep holding if wazero fixes the backend.
var knownEngineDivergences = map[string]string{
	"ranking/negation_polarity/affirmative_paraphrase": "wazero v1.12.0 amd64 compiler: 0.49 vs interpreter 0.37",
	"ranking/negation_polarity/unrelated":              "wazero v1.12.0 amd64 compiler: 0.49 vs interpreter 0.15",
}

type engineInput struct {
	key         string
	question    string
	groundTruth string
	answer      string
}

func engineInputs(t *testing.T) []engineInput {
	t.Helper()
	var inputs []engineInput

	fixture := loadFixture(t)
	for _, c := range fixture.Cases {
		inputs = append(inputs, engineInput{
			key:         "case/" + c.CaseID,
			question:    c.Question,
			groundTruth: c.GroundTruth,
			answer:      c.answer(t),
		})
	}
	// The factual pairs carry the margins on record with Telegraph, so they are
	// the inputs where an engine-dependent score would matter most.
	for _, p := range fixture.FactualPairs {
		inputs = append(inputs,
			engineInput{
				key:         "pair/" + p.PairID + "/good",
				question:    p.Question,
				groundTruth: p.GroundTruth,
				answer:      p.GoodAnswer,
			},
			engineInput{
				key:         "pair/" + p.PairID + "/bad",
				question:    p.Question,
				groundTruth: p.GroundTruth,
				answer:      p.BadAnswer,
			},
		)
	}
	for _, pool := range loadRankingPools(t).Pools {
		for _, c := range pool.Candidates {
			inputs = append(inputs, engineInput{
				key:         "ranking/" + pool.PoolID + "/" + c.Label,
				question:    pool.Question,
				groundTruth: pool.GroundTruth,
				answer:      c.Answer,
			})
		}
	}
	if len(inputs) < 90 {
		t.Fatalf("engine agreement corpus has %d inputs, want at least 90", len(inputs))
	}
	return inputs
}

func scoreAllWithEngine(t *testing.T, engine wasmEngine, inputs []engineInput) map[string]float32 {
	t.Helper()
	scorer, err := openScorerWithEngine(wasmPath(t), engine)
	if err != nil {
		t.Fatalf("%s: %v", engine, err)
	}
	defer scorer.close()

	scores := make(map[string]float32, len(inputs))
	for _, in := range inputs {
		value, err := scorer.score(in.question, in.groundTruth, in.answer)
		if err != nil {
			t.Fatalf("%s scoring %s: %v", engine, in.key, err)
		}
		scores[in.key] = value
	}
	return scores
}

func TestEngineScoresAgree(t *testing.T) {
	inputs := engineInputs(t)
	compiled := scoreAllWithEngine(t, engineCompiler, inputs)
	interpreted := scoreAllWithEngine(t, engineInterpreter, inputs)

	var diverged []string
	for _, in := range inputs {
		if compiled[in.key] != interpreted[in.key] {
			diverged = append(diverged, in.key)
			t.Logf("ENGINE DIVERGENCE %s: compiler %.6f, interpreter %.6f",
				in.key, compiled[in.key], interpreted[in.key])
		}
	}
	sort.Strings(diverged)
	t.Logf("ENGINE %d inputs, %d diverge between compiler and interpreter",
		len(inputs), len(diverged))

	for _, key := range diverged {
		if _, known := knownEngineDivergences[key]; !known {
			t.Errorf("%s scores differ between wazero's compiler (%.6f) and "+
				"interpreter (%.6f). A score that depends on the engine is not a "+
				"property of the module, so it cannot be quoted as evidence. "+
				"Investigate before recording it as known.",
				key, compiled[key], interpreted[key])
		}
	}
}

// TestRegistrationMarginsHoldOnBothEngines checks the specific claim that
// registration rests on: every factual pair clears its minimum margin whichever
// engine runs the module. TestFixtureCorpus already asserts this on wazero's
// default engine; the point here is that the margin does not depend on that
// default. This is deliberately separate from TestEngineScoresAgree, because a
// score may legitimately differ by a hair without moving a margin across its
// floor, and only the second of those is a registration problem.
func TestRegistrationMarginsHoldOnBothEngines(t *testing.T) {
	fixture := loadFixture(t)
	if len(fixture.FactualPairs) == 0 {
		t.Fatal("fixture declares no factual pairs")
	}

	for _, engine := range []wasmEngine{engineCompiler, engineInterpreter} {
		t.Run(fmt.Sprint(engine), func(t *testing.T) {
			scorer, err := openScorerWithEngine(wasmPath(t), engine)
			if err != nil {
				t.Fatal(err)
			}
			defer scorer.close()

			for _, pair := range fixture.FactualPairs {
				good, err := scorer.score(pair.Question, pair.GroundTruth, pair.GoodAnswer)
				if err != nil {
					t.Fatalf("%s good: %v", pair.PairID, err)
				}
				bad, err := scorer.score(pair.Question, pair.GroundTruth, pair.BadAnswer)
				if err != nil {
					t.Fatalf("%s bad: %v", pair.PairID, err)
				}
				if margin := good - bad; margin < pair.MinimumMargin {
					t.Errorf("%s margin %.6f below required %.6f (good %.6f, bad %.6f)",
						pair.PairID, margin, pair.MinimumMargin, good, bad)
				}
				if pair.MaximumBadScore != nil && bad > *pair.MaximumBadScore {
					t.Errorf("%s bad score %.6f exceeds maximum %.6f",
						pair.PairID, bad, *pair.MaximumBadScore)
				}
			}
		})
	}
}
