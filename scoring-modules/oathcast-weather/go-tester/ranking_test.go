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
// They were LOOSENED on 2026-08-19, which is the thing a ratchet exists to prevent,
// so the reason is recorded here rather than in a commit message alone.
//
// Two changes had taken these numbers to 10 separated with no ties and no
// inversions, a worst separation of +0.048125. Registration 98 shipped that build
// and won 28 of Telegraph's 32 fixture cases, where the three previous builds all
// won 31. Both changes converted a signal that capped a score into one that zeroes
// it, so a correct answer tripping either fell from a capped score that could still
// beat its paired wrong answer to zero, which cannot. Score stddev rose from
// 0.29768366 to 0.33863735, which is what a build with more zeros looks like.
//
// Both were reverted. The scorer now rebuilds byte-identical to the registration 96
// artifact, 9183cbde, which is the best externally measured state at 31 of 32. The
// tie and the inversion below come back with it:
//
//	authorship_entity_swap    +0.000000  a correct and a wrong answer both pinned to
//	                                     the 0.49 ambiguity ceiling
//	shared_token_distractor   -0.045833  "Portuguese is spoken in Portugal" outranks
//	                                     the correct "Brazilians speak Portuguese"
//
// The lesson is not that those defects are acceptable. It is that this corpus was
// authored by us, so it cannot see a rule that misfires only on shapes we did not
// think of. Across four registrations these local numbers improved monotonically
// while the scored result went 31, 31, 31, 28. A rising number here is evidence
// about the properties encoded here and about nothing else.
//
// Telegraph's team has said they are publishing a champion baseline that gives the
// scoring surface to test against locally. Tighten these again when a fix can be
// checked against that rather than against pools we wrote, and prefer a cap over a
// zero unless a contradiction is certain, because a zero can only ever remove a win
// a cap might have kept.
const (
	minSeparatedPools     = 8
	minWorstSeparation    = -0.0459
	maxBoundaryTies       = 1
	maxBoundaryInversions = 1
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
