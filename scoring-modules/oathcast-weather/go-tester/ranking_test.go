package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// The scorer's job is to rank a pool of candidate answers, not to clear a
// fixture threshold. Telegraph's team put it directly: the value of the module is
// how well it ranks miners, so a better scorer improves the whole network's
// ranking of intelligence. This benchmark measures that property against the real
// WASM through wazero, so it reports what a validator would actually see.
//
// The decisive number is separation: the worst correct answer minus the best
// wrong one. Positive means the pool is ordered usefully. Zero means the scorer
// cannot tell right from wrong. Negative means it prefers a wrong answer.
//
// This runs on wazero's interpreter rather than its default compiler, because
// the compiler's amd64 backend has been measured scoring two of these
// candidates differently from every other os/arch/engine combination. A ranking
// number that changes with the validator's CPU describes the runtime, not the
// module. TestEngineScoresAgree pins that divergence; the floors below are
// engine-independent and hold identically on arm64 and amd64.

// These floors record MEASURED CURRENT QUALITY and are a ratchet: a scoring change
// that degrades ranking fails here rather than passing quietly.
//
// Every pool now separates. That was not true when this benchmark was written: the
// first run reported 7 separated, 1 tied and 2 inverted, with a worst separation of
// -0.4925. Four fixes closed the gap, and each was found by this measurement rather
// than by the pre-existing suite, which stayed green through all of them:
//
//	entity binding      A truth that restates its question left no anchor, so no
//	                    entity check ran at all and a swapped subject scored
//	                    0.862500 against a correct paraphrase's 0.370000.
//	weather classifier  "Sun" is in the CLEAR synonym group, so a binary
//	                    general-knowledge question was scored as a forecast and the
//	                    probability ceiling pinned a correct answer at 0.490000.
//	relation reassigned A ceiling flattened a correct and a wrong answer onto
//	                    exactly 0.490000. Ties lose a case just as inversions do.
//	slot substitution   With an anchor present and satisfied, exchanging the entity
//	                    behind the same preposition went unpunished, so "spoken in
//	                    Portugal" outranked a correct paraphrase.
//
// The remaining known defect is not visible in these pools: predicate substitution,
// where the answer keeps the entity and replaces the truth's distinguishing
// predicate with a non-equivalent one. Separating that from a legitimate paraphrase
// needs synonymy this module cannot judge, so it is tracked in
// generated_pair_scoreboard rather than papered over here.
const (
	minSeparatedPools     = 10
	minWorstSeparation    = 0.0480
	maxBoundaryTies       = 0
	maxBoundaryInversions = 0
	minPairwiseAccuracy   = 0.9400
)

type rankingCandidate struct {
	Label   string `json:"label"`
	Answer  string `json:"answer"`
	Correct bool   `json:"correct"`
	Quality int    `json:"quality"`
	Note    string `json:"note"`
}

type rankingPool struct {
	PoolID      string             `json:"pool_id"`
	Question    string             `json:"question"`
	GroundTruth string             `json:"ground_truth"`
	Candidates  []rankingCandidate `json:"candidates"`
}

type rankingFixture struct {
	Schema string        `json:"schema"`
	Pools  []rankingPool `json:"pools"`
}

func loadRankingPools(t *testing.T) rankingFixture {
	t.Helper()
	path := filepath.Join(repoRoot(t), "fixtures", "scorer_ranking_pools.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture rankingFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Schema != "oathcast_scorer_ranking_pools_v1" {
		t.Fatalf("unexpected ranking fixture schema %q", fixture.Schema)
	}
	if len(fixture.Pools) < 8 {
		t.Fatalf("ranking fixture has %d pools, want at least 8", len(fixture.Pools))
	}
	return fixture
}

func TestScorerRanksCandidatePools(t *testing.T) {
	scorer, err := openScorerWithEngine(wasmPath(t), engineInterpreter)
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	fixture := loadRankingPools(t)
	separated := 0
	ties := 0
	inversions := 0
	worstSeparation := 1.0
	worstPool := ""
	var pairsTotal, pairsCorrect int

	for _, pool := range fixture.Pools {
		if len(pool.Candidates) < 3 {
			t.Fatalf("pool %s has %d candidates, want at least 3", pool.PoolID, len(pool.Candidates))
		}
		type scored struct {
			cand  rankingCandidate
			score float32
		}
		results := make([]scored, 0, len(pool.Candidates))
		for _, candidate := range pool.Candidates {
			value, err := scorer.score(pool.Question, pool.GroundTruth, candidate.Answer)
			if err != nil {
				t.Fatalf("pool %s candidate %s: %v", pool.PoolID, candidate.Label, err)
			}
			results = append(results, scored{cand: candidate, score: value})
		}

		// Pairwise ordering accuracy over the graded quality ranks. Only pairs
		// with a genuine quality difference are counted; equal ranks carry no
		// expectation about their order.
		for i := range results {
			for j := range results {
				if i >= j || results[i].cand.Quality == results[j].cand.Quality {
					continue
				}
				pairsTotal++
				better, worse := results[i], results[j]
				if worse.cand.Quality > better.cand.Quality {
					better, worse = worse, better
				}
				if better.score > worse.score {
					pairsCorrect++
				}
			}
		}

		worstCorrect := float32(1)
		bestWrong := float32(0)
		haveCorrect, haveWrong := false, false
		for _, item := range results {
			if item.cand.Correct {
				haveCorrect = true
				if item.score < worstCorrect {
					worstCorrect = item.score
				}
			} else {
				haveWrong = true
				if item.score > bestWrong {
					bestWrong = item.score
				}
			}
		}
		if !haveCorrect || !haveWrong {
			t.Fatalf("pool %s needs at least one correct and one wrong candidate", pool.PoolID)
		}

		gap := float64(worstCorrect - bestWrong)
		switch {
		case gap > 1e-6:
			separated++
		case gap < -1e-6:
			inversions++
		default:
			ties++
		}
		if gap < worstSeparation {
			worstSeparation = gap
			worstPool = pool.PoolID
		}

		sort.SliceStable(results, func(a, b int) bool { return results[a].score > results[b].score })
		verdict := "separated"
		if gap < -1e-6 {
			verdict = "INVERTED"
		} else if gap <= 1e-6 {
			verdict = "TIED"
		}
		t.Logf("pool %-28s separation %+.6f  %s", pool.PoolID, gap, verdict)
		for _, item := range results {
			mark := "wrong  "
			if item.cand.Correct {
				mark = "correct"
			}
			t.Logf("    %.6f  q%d  %s  %s", item.score, item.cand.Quality, mark, item.cand.Label)
		}
	}

	accuracy := 0.0
	if pairsTotal > 0 {
		accuracy = float64(pairsCorrect) / float64(pairsTotal)
	}
	t.Logf("RANKING pools=%d separated=%d tied=%d inverted=%d",
		len(fixture.Pools), separated, ties, inversions)
	t.Logf("RANKING worst separation %+.6f (%s)", worstSeparation, worstPool)
	t.Logf("RANKING pairwise ordering accuracy %d/%d = %.4f", pairsCorrect, pairsTotal, accuracy)

	if separated < minSeparatedPools {
		t.Errorf("only %d pools separate correct from wrong, want at least %d",
			separated, minSeparatedPools)
	}
	if inversions > maxBoundaryInversions {
		t.Errorf("%d pools rank a wrong answer above every correct one, want at most %d",
			inversions, maxBoundaryInversions)
	}
	if ties > maxBoundaryTies {
		t.Errorf("%d pools cannot distinguish correct from wrong, want at most %d",
			ties, maxBoundaryTies)
	}
	if worstSeparation < minWorstSeparation {
		t.Errorf("worst separation %+.6f is below the recorded floor %+.6f",
			worstSeparation, minWorstSeparation)
	}
	if accuracy < minPairwiseAccuracy {
		t.Errorf("pairwise ordering accuracy %.4f is below the recorded floor %.4f",
			accuracy, minPairwiseAccuracy)
	}
}
