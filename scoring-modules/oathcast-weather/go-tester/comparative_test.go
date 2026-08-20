package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// Held-out generalisation measurement, and a three-way comparison against
// Telegraph's published champion baseline.
//
// This exists because of a specific failure. Across four registrations the local
// numbers improved monotonically, generated-pair inversions falling from 66 of 375
// to 9 and the handwritten pools going from 7 separated to 10, while Telegraph's
// scored result went 31, 31, 31, 28. Every suite was green at every step. The
// missing instrument was not a better metric but an *independent* one: every
// corpus in this repository was authored alongside the rules it tests, so a rule
// that misfires only on shapes nobody thought of is invisible to all of them.
//
// fixtures/scorer_heldout_pairs.json is written to be independent in the one way
// that matters. Each shape is deliberately one that no rule in scorer.rs was built
// to detect: process reversal, comparative reframing, misspellings, scope
// overclaiming, hedged-correct against confident-wrong, definition inversion,
// register shifts, list membership, temporal order and causal direction. Accuracy
// there measures generalisation. Accuracy on the ranking pools measures whether our
// own rules still fire.
//
// The champion is not vendored. It is 24 MB and its repository carries no LICENSE
// file, so it cannot be redistributed here; running it locally as an oracle is a
// different matter and is what the team suggested. Point OATHCAST_CHAMPION_WASM at
// a local build to enable the comparison:
//
//	git clone https://github.com/telegraphprotocol/telegraph-wasm-baseline
//	cd telegraph-wasm-baseline
//	cargo build --release --target wasm32-unknown-unknown --features real_weights
//	OATHCAST_CHAMPION_WASM=$PWD/target/wasm32-unknown-unknown/release/telegraph_scoring.wasm \
//	  go test -run TestChampionComparison -v ./...
//
// Note --features real_weights. Without it the repository builds a projection
// fallback that hashes token ids into pseudo-embeddings with no semantic meaning,
// which would produce a comparison that looks plausible and measures nothing.
// TestChampionComparison checks for that explicitly rather than trusting the flag.

type heldOutPair struct {
	PairID     string `json:"pair_id"`
	Shape      string `json:"shape"`
	Question   string `json:"question"`
	Truth      string `json:"ground_truth"`
	GoodAnswer string `json:"good_answer"`
	BadAnswer  string `json:"bad_answer"`
	WhyWrong   string `json:"why_bad_is_wrong"`
}

type heldOutFixture struct {
	Schema string        `json:"schema"`
	Pairs  []heldOutPair `json:"pairs"`
}

func loadHeldOutPairs(t *testing.T) heldOutFixture {
	t.Helper()
	path := filepath.Join(repoRoot(t), "fixtures", "scorer_heldout_pairs.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture heldOutFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Schema != "oathcast_scorer_heldout_pairs_v1" {
		t.Fatalf("unexpected held-out fixture schema %q", fixture.Schema)
	}
	if len(fixture.Pairs) < 25 {
		t.Fatalf("held-out fixture has %d pairs, want at least 25", len(fixture.Pairs))
	}
	// Every row must justify its label. Two earlier corpora in this repository
	// shipped rows whose "wrong" answer was actually true, and each cost real time
	// before being traced back to the table rather than the scorer.
	for _, pair := range fixture.Pairs {
		if pair.WhyWrong == "" {
			t.Fatalf("pair %s does not say why its bad answer is wrong", pair.PairID)
		}
		if pair.GoodAnswer == pair.BadAnswer {
			t.Fatalf("pair %s has identical good and bad answers", pair.PairID)
		}
	}
	return fixture
}

type shapeTally struct {
	total, correct int
}

// scoreHeldOut returns, per pair, whether the scorer ranked good above bad, plus
// the raw margin.
func scoreHeldOut(t *testing.T, path string, engine wasmEngine, pairs []heldOutPair) (map[string]bool, map[string]float64) {
	t.Helper()
	scorer, err := openScorerWithEngine(path, engine)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer scorer.close()

	ordered := make(map[string]bool, len(pairs))
	margins := make(map[string]float64, len(pairs))
	for _, pair := range pairs {
		good, err := scorer.score(pair.Question, pair.Truth, pair.GoodAnswer)
		if err != nil {
			t.Fatalf("%s good: %v", pair.PairID, err)
		}
		bad, err := scorer.score(pair.Question, pair.Truth, pair.BadAnswer)
		if err != nil {
			t.Fatalf("%s bad: %v", pair.PairID, err)
		}
		ordered[pair.PairID] = good > bad
		margins[pair.PairID] = float64(good - bad)
	}
	return ordered, margins
}

func tallyByShape(pairs []heldOutPair, ordered map[string]bool) (map[string]*shapeTally, []string) {
	byShape := map[string]*shapeTally{}
	var order []string
	for _, pair := range pairs {
		tally, seen := byShape[pair.Shape]
		if !seen {
			tally = &shapeTally{}
			byShape[pair.Shape] = tally
			order = append(order, pair.Shape)
		}
		tally.total++
		if ordered[pair.PairID] {
			tally.correct++
		}
	}
	return byShape, order
}

// minHeldOutOrdered is a ratchet on GENERALISATION, the property no other test in
// this package covers. It is emphatically NOT a statement that the current
// behaviour is good. 9 of 30 is worse than a coin flip.
//
// I first wrote 21 here, before measuring, which is the same error as picking a
// floor from hope rather than evidence. The measured value is 9, and it is recorded
// as 9 so that the number in the file is a fact rather than an aspiration.
//
// The 9 matters because of the contrast. On corpora authored in this repository the
// scorer is close to perfect: 10 of 10 ranking pools separating and 366 of 375
// generated pairs ordered. On shapes with no corresponding rule it manages 9 of 30.
// That gap is the explanation for the registration series, where five defect fixes
// moved Telegraph's per-case count by zero: the scorer does not generalise, it
// recognises the specific defect shapes someone wrote a rule for.
//
// Measured on the same 30 pairs, Telegraph's champion baseline orders 3 correctly.
// Neither architecture handles these shapes, and 20 of the 30 defeat both. That is
// not a reason for comfort in either direction, and specifically it is not evidence
// that this module is the better judge; see champion_baseline_comparison in
// release-evidence.json for why three corpora give three different verdicts.
const minHeldOutOrdered = 9

func TestHeldOutPairAccuracy(t *testing.T) {
	fixture := loadHeldOutPairs(t)
	ordered, margins := scoreHeldOut(t, wasmPath(t), engineInterpreter, fixture.Pairs)

	byShape, order := tallyByShape(fixture.Pairs, ordered)
	total := 0
	for _, shape := range order {
		tally := byShape[shape]
		total += tally.correct
		t.Logf("HELDOUT %-28s %d/%d", shape, tally.correct, tally.total)
	}
	t.Logf("HELDOUT TOTAL %d/%d ordered correctly (%.1f%%)",
		total, len(fixture.Pairs), 100*float64(total)/float64(len(fixture.Pairs)))

	// Name the misses. A bare total invites the assumption that the remainder is
	// noise, and these are the shapes a future fix should be measured against.
	var missed []string
	for _, pair := range fixture.Pairs {
		if !ordered[pair.PairID] {
			missed = append(missed, pair.PairID)
		}
	}
	sort.Strings(missed)
	for _, id := range missed {
		t.Logf("HELDOUT missed %s (margin %+.6f)", id, margins[id])
	}

	if total < minHeldOutOrdered {
		t.Errorf("only %d of %d held-out pairs ordered correctly, want at least %d. "+
			"These shapes have no corresponding rule, so a drop here is a loss of "+
			"generalisation rather than a rule regression.",
			total, len(fixture.Pairs), minHeldOutOrdered)
	}
}

func TestChampionComparison(t *testing.T) {
	championPath := os.Getenv("OATHCAST_CHAMPION_WASM")
	if championPath == "" {
		// A skip that reads like a pass is the failure mode this repository has hit
		// most often, so say plainly what did not run and why.
		t.Log("CHAMPION COMPARISON DID NOT RUN: OATHCAST_CHAMPION_WASM is unset. " +
			"This is not a pass. The champion baseline is 24 MB and its repository " +
			"carries no LICENSE file, so it is deliberately not vendored here. See " +
			"the file header for the build command.")
		t.Skip("OATHCAST_CHAMPION_WASM unset")
	}
	// If the variable is set but wrong, fail rather than skip. A typo silently
	// disabling the only independent check would defeat the point of having it.
	if _, err := os.Stat(championPath); err != nil {
		t.Fatalf("OATHCAST_CHAMPION_WASM is set to %q but cannot be read: %v",
			championPath, err)
	}

	fixture := loadHeldOutPairs(t)

	// Guard against the projection-mode trap. Built without --features
	// real_weights the baseline hashes token ids into pseudo-embeddings of the
	// right shape and no meaning, and a comparison against that measures nothing
	// while looking entirely reasonable. A genuine sentence transformer scores a
	// paraphrase with almost no shared vocabulary far above unrelated text; the
	// projection fallback cannot.
	champion, err := openScorerWithEngine(championPath, engineCompiler)
	if err != nil {
		t.Fatalf("open champion: %v", err)
	}
	const (
		semQuestion   = "What is the tallest mountain on Earth?"
		semTruth      = "Everest is the tallest mountain on Earth."
		semParaphrase = "Everest is the highest peak on the planet."
		semUnrelated  = "Bananas are yellow when they are ripe."
	)
	paraphrase, err := champion.score(semQuestion, semTruth, semParaphrase)
	if err != nil {
		t.Fatalf("champion semantic probe: %v", err)
	}
	unrelated, err := champion.score(semQuestion, semTruth, semUnrelated)
	if err != nil {
		t.Fatalf("champion semantic probe: %v", err)
	}
	champion.close()
	t.Logf("CHAMPION semantic probe: paraphrase %.6f, unrelated %.6f", paraphrase, unrelated)
	if paraphrase-unrelated < 0.25 {
		t.Fatalf("champion at %s does not separate a low-overlap paraphrase (%.6f) from "+
			"unrelated text (%.6f) by a semantic margin. It was probably built without "+
			"--features real_weights, which yields hashed pseudo-embeddings. Comparing "+
			"against that would measure nothing.",
			championPath, paraphrase, unrelated)
	}

	oursOrdered, oursMargins := scoreHeldOut(t, wasmPath(t), engineInterpreter, fixture.Pairs)
	champOrdered, champMargins := scoreHeldOut(t, championPath, engineCompiler, fixture.Pairs)

	oursByShape, order := tallyByShape(fixture.Pairs, oursOrdered)
	champByShape, _ := tallyByShape(fixture.Pairs, champOrdered)

	oursTotal, champTotal := 0, 0
	for _, shape := range order {
		o, c := oursByShape[shape], champByShape[shape]
		oursTotal += o.correct
		champTotal += c.correct
		t.Logf("COMPARE %-28s ours %d/%d   champion %d/%d", shape, o.correct, o.total, c.correct, c.total)
	}
	n := len(fixture.Pairs)
	t.Logf("COMPARE TOTAL ours %d/%d (%.1f%%)   champion %d/%d (%.1f%%)",
		oursTotal, n, 100*float64(oursTotal)/float64(n),
		champTotal, n, 100*float64(champTotal)/float64(n))

	// The disagreements are the point. Where one ranks correctly and the other does
	// not, the pair names a capability one has and the other lacks, which is what a
	// hybrid would have to preserve from each side.
	var oursOnly, champOnly, bothMissed []string
	for _, pair := range fixture.Pairs {
		switch o, c := oursOrdered[pair.PairID], champOrdered[pair.PairID]; {
		case o && !c:
			oursOnly = append(oursOnly, pair.PairID)
		case !o && c:
			champOnly = append(champOnly, pair.PairID)
		case !o && !c:
			bothMissed = append(bothMissed, pair.PairID)
		}
	}
	for _, group := range []struct {
		label string
		ids   []string
	}{
		{"ONLY OURS", oursOnly},
		{"ONLY CHAMPION", champOnly},
		{"NEITHER", bothMissed},
	} {
		sort.Strings(group.ids)
		t.Logf("COMPARE %s ordered correctly: %d", group.label, len(group.ids))
		for _, id := range group.ids {
			t.Logf("    %-38s ours %+.6f   champion %+.6f", id, oursMargins[id], champMargins[id])
		}
	}

	// Deliberately no assertion on the champion's numbers. They are not ours to
	// control, and a floor on someone else's module would fail for reasons that say
	// nothing about this repository. This test reports; TestHeldOutPairAccuracy is
	// where the ratchet lives.
}
