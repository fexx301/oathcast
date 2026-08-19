package main

import (
	"fmt"
	"sort"
	"testing"
)

// Exposure estimate for the wazero amd64 compiler divergence.
//
// TestEngineScoresAgree measures the divergence rate on the curated corpus: 1 of
// 256 inputs. That number understates the risk that matters, because those inputs
// were not written to sit near a decision boundary, and Telegraph's Stage 2
// near-miss cases are selected for exactly that. A score shifting by 0.12 changes
// nothing in the middle of the range and changes the verdict at the edge.
//
// So this generates pairs across the shapes a near-miss case takes, finds the
// ones whose margin lands close to the 0.15 floor, and asks a sharper question
// than "do scores differ": does the pass/fail verdict differ, and in which
// direction did the margin move.
//
// Measured on amd64, the answer is 22.4 percent of pairs diverge, 17 of the 20
// pairs near the floor diverge, no verdict flips, and every divergence widened
// the margin. That last part is not a safety property. The compiler pulls low
// scores up to exactly 0.490000 regardless of correctness, and these templates
// make the correct answer the lexically distant, lower-scoring side, so widening
// is an artefact of the generator. In the curated pools the opposite occurred
// once: a wrong answer rose from 0.150000 and narrowed a margin.
//
// Telegraph confirmed on 2026-08-19 that they run a single validator and will not
// move to deterministic execution until multi-validator scaling, because
// determinism costs 10 to 50 times the speed. That ratio is the
// interpreter-versus-compiler tradeoff, so their validator is very likely the
// diverging configuration, and waiting will not close this.
//
// On arm64 every count here is zero, which is the correct answer for arm64 and
// not evidence that the bug is harmless. Run it on amd64 to see the exposure.

// Stage 2's margin floor, as confirmed by Telegraph: good - bad >= 0.15.
const stageTwoMarginFloor = 0.15

// nearBoundaryBand selects pairs whose reference margin sits within this
// distance of the floor. Wide enough to hold a useful sample, narrow enough that
// a 0.12 shift can plausibly cross the floor from either side.
const nearBoundaryBand = 0.10

type generatedPair struct {
	id       string
	question string
	truth    string
	good     string
	bad      string
}

// generateBoundaryPairs builds pairs from templates crossed with entity tuples.
// Deterministic and order-stable: no randomness, so a divergence found here is
// reproducible by rerunning the test.
func generateBoundaryPairs() []generatedPair {
	type subject struct {
		// entity is the subject the question asks about; rival is a plausible
		// wrong substitute of the same kind; attr and rivalAttr are the property
		// each one actually has.
		entity, rival, attr, rivalAttr, kind string
	}
	// rivalAttr must differ from attr in every row, and must be genuinely false of
	// the entity. Two rows failed that. Brazil originally carried "Portuguese" for
	// both, which made the attribute_swap wrong answer identical to the ground
	// truth so it scored 1.0. Tokyo's rival attribute was "a port city in Japan",
	// which Tokyo is, so a true statement was being counted as a wrong answer.
	// Both charged inversions to the scorer that belonged to this table.
	subjects := []subject{
		{"Everest", "K2", "the tallest mountain on Earth", "the second tallest mountain on Earth", "peak"},
		{"Brazil", "Portugal", "the largest country in South America", "the westernmost country in Iberia", "country"},
		{"Shakespeare", "Marlowe", "Hamlet", "Doctor Faustus", "author"},
		{"Pacific", "Atlantic", "the deepest ocean", "the second largest ocean", "ocean"},
		{"Tokyo", "Osaka", "the capital of Japan", "a small fishing village in Japan", "city"},
		{"Amazon", "Nile", "the largest river by discharge", "the longest river", "river"},
		{"Mercury", "Venus", "the closest planet to the Sun", "the second planet from the Sun", "planet"},
		{"Vatican", "Monaco", "the smallest sovereign state", "the second smallest sovereign state", "state"},
		{"Curie", "Meitner", "the first woman to win a Nobel Prize", "a discoverer of nuclear fission", "scientist"},
		{"Sahara", "Gobi", "the largest hot desert", "a cold desert in Asia", "desert"},
		{"Nile", "Congo", "the longest river in Africa", "the deepest river in Africa", "river"},
		{"Antarctica", "Greenland", "the coldest continent", "the largest island", "continent"},
		{"Jupiter", "Saturn", "the largest planet", "the planet with the most visible rings", "planet"},
		{"Latin", "Greek", "the language of ancient Rome", "the language of ancient Athens", "language"},
		{"Beethoven", "Brahms", "the Ninth Symphony", "the German Requiem", "composer"},
	}

	// Each template returns a question, a ground truth, a correct answer that is
	// worded differently from the truth, and a wrong answer that stays close to
	// the truth's wording. That asymmetry is the point: it is the shape that
	// puts a margin near the floor.
	//
	// terseOK marks templates whose correct answer survives being reduced to the
	// bare entity. It is false where correctness lives in the polarity rather
	// than the entity, because "K2." is not a correct answer to "Is K2 the
	// tallest mountain?" no matter how the truth is worded.
	templates := []struct {
		name    string
		terseOK bool
		make    func(subject) (q, t, good, bad string)
	}{
		{"identity_binary", false, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("Is %s %s?", s.entity, s.attr),
				fmt.Sprintf("Yes, %s is %s.", s.entity, s.attr),
				fmt.Sprintf("%s does hold that distinction.", s.entity),
				fmt.Sprintf("Yes, %s is %s.", s.rival, s.attr)
		}},
		{"identity_open", true, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("What is %s?", s.attr),
				fmt.Sprintf("%s is %s.", s.entity, s.attr),
				fmt.Sprintf("That would be %s.", s.entity),
				fmt.Sprintf("%s is %s.", s.rival, s.attr)
		}},
		{"attribute_swap", false, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("What is %s known for?", s.entity),
				fmt.Sprintf("%s is known as %s.", s.entity, s.attr),
				fmt.Sprintf("%s holds the record: %s.", s.entity, s.attr),
				fmt.Sprintf("%s is known as %s.", s.entity, s.rivalAttr)
		}},
		{"relation_direction", true, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("Which %s is associated with %s?", s.kind, s.attr),
				fmt.Sprintf("%s is associated with %s.", s.entity, s.attr),
				fmt.Sprintf("The answer is %s.", s.entity),
				fmt.Sprintf("%s is associated with %s.", s.rival, s.attr)
		}},
		{"negation_polarity", false, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("Is %s %s?", s.rival, s.attr),
				fmt.Sprintf("No, %s is not %s.", s.rival, s.attr),
				fmt.Sprintf("%s does not hold that distinction.", s.rival),
				fmt.Sprintf("Yes, %s is %s.", s.rival, s.attr)
		}},
		{"terse_versus_verbose", true, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("Which %s is %s?", s.kind, s.attr),
				fmt.Sprintf("%s is %s.", s.entity, s.attr),
				s.entity + ".",
				fmt.Sprintf("Considering every %s in turn, the one that is %s is clearly %s.",
					s.kind, s.attr, s.rival)
		}},
		{"shared_token_distractor", true, func(s subject) (string, string, string, string) {
			return fmt.Sprintf("Which %s is %s?", s.kind, s.attr),
				fmt.Sprintf("%s is %s.", s.entity, s.attr),
				fmt.Sprintf("Among all of them, %s.", s.entity),
				fmt.Sprintf("%s is %s.", s.rival, s.rivalAttr)
		}},
	}

	// Rewordings of the correct answer, all still correct, at increasing lexical
	// distance from the ground truth. Their purpose is to sweep the margin
	// through a range rather than cluster it: a fixed set of templates lands
	// wherever it lands, and a sample that never approaches the floor cannot say
	// anything about behaviour at the floor.
	variants := []struct {
		name  string
		terse bool
		apply func(s subject, good string) string
	}{
		{"asis", false, func(_ subject, good string) string { return good }},
		{"terse", true, func(s subject, _ string) string { return s.entity + "." }},
		{"hedged", false, func(_ subject, good string) string {
			return "Based on the record, " + good
		}},
		{"padded", false, func(_ subject, good string) string {
			return good + " That has been the established position for a long time, " +
				"and nothing in the available material contradicts it."
		}},
	}

	var pairs []generatedPair
	for _, s := range subjects {
		if s.attr == s.rivalAttr {
			panic("subject " + s.entity + " has rivalAttr equal to attr, which makes " +
				"the attribute_swap wrong answer identical to the ground truth")
		}
	}
	for _, tmpl := range templates {
		for i, s := range subjects {
			q, t, good, bad := tmpl.make(s)
			for _, v := range variants {
				if v.terse && !tmpl.terseOK {
					continue
				}
				pairs = append(pairs, generatedPair{
					id:       fmt.Sprintf("%s/%02d_%s/%s", tmpl.name, i, s.entity, v.name),
					question: q,
					truth:    t,
					good:     v.apply(s, good),
					bad:      bad,
				})
			}
		}
	}
	return pairs
}

type pairScores struct {
	good, bad float32
}

func scorePairsWithEngine(t *testing.T, engine wasmEngine, pairs []generatedPair) map[string]pairScores {
	t.Helper()
	scorer, err := openScorerWithEngine(wasmPath(t), engine)
	if err != nil {
		t.Fatalf("%s: %v", engine, err)
	}
	defer scorer.close()

	out := make(map[string]pairScores, len(pairs))
	for _, p := range pairs {
		good, err := scorer.score(p.question, p.truth, p.good)
		if err != nil {
			t.Fatalf("%s %s good: %v", engine, p.id, err)
		}
		bad, err := scorer.score(p.question, p.truth, p.bad)
		if err != nil {
			t.Fatalf("%s %s bad: %v", engine, p.id, err)
		}
		out[p.id] = pairScores{good: good, bad: bad}
	}
	return out
}

// maxEngineVerdictFlips is a recorded ceiling, measured not chosen. A flip is a
// generated pair that clears the Stage 2 margin floor under one engine and
// fails it under the other, which is the concrete way this bug could cost a
// case. Raising this number requires an explanation.
const maxEngineVerdictFlips = 0

func TestNearBoundaryEngineExposure(t *testing.T) {
	pairs := generateBoundaryPairs()
	if len(pairs) < 100 {
		t.Fatalf("generated %d pairs, want at least 100", len(pairs))
	}

	reference := scorePairsWithEngine(t, engineInterpreter, pairs)
	compiled := scorePairsWithEngine(t, engineCompiler, pairs)

	var (
		scoreDiverged  []string
		nearBoundary   int
		nearDiverged   int
		verdictFlips   []string
		worstShift     float64
		worstShiftPair string
		marginWidened  int
		marginNarrowed int
	)

	for _, p := range pairs {
		ref, cmp := reference[p.id], compiled[p.id]
		refMargin := float64(ref.good - ref.bad)
		cmpMargin := float64(cmp.good - cmp.bad)

		differs := ref.good != cmp.good || ref.bad != cmp.bad
		if differs {
			scoreDiverged = append(scoreDiverged, p.id)
			// The sign matters more than the magnitude. A divergence that widens
			// every margin is a windfall; one that narrows them is the thing that
			// costs a case. Counted rather than inferred, because the direction
			// depends on which side of the pair happened to score low, and these
			// templates make the correct answer the lexically distant one, which
			// biases the sample toward widening.
			if cmpMargin > refMargin {
				marginWidened++
			} else if cmpMargin < refMargin {
				marginNarrowed++
			}
			shift := cmpMargin - refMargin
			if shift < 0 {
				shift = -shift
			}
			if shift > worstShift {
				worstShift, worstShiftPair = shift, p.id
			}
		}

		near := refMargin-stageTwoMarginFloor <= nearBoundaryBand &&
			stageTwoMarginFloor-refMargin <= nearBoundaryBand
		if near {
			nearBoundary++
			if differs {
				nearDiverged++
			}
		}

		if (refMargin >= stageTwoMarginFloor) != (cmpMargin >= stageTwoMarginFloor) {
			verdictFlips = append(verdictFlips, p.id)
			t.Logf("EXPOSURE VERDICT FLIP %s: interpreter margin %+.6f, compiler margin %+.6f",
				p.id, refMargin, cmpMargin)
		}
	}
	sort.Strings(scoreDiverged)
	sort.Strings(verdictFlips)

	t.Logf("EXPOSURE %d generated pairs, %d with a diverging score (%.2f%%)",
		len(pairs), len(scoreDiverged),
		100*float64(len(scoreDiverged))/float64(len(pairs)))
	t.Logf("EXPOSURE %d pairs within %.2f of the %.2f floor, %d of those diverge",
		nearBoundary, nearBoundaryBand, stageTwoMarginFloor, nearDiverged)
	t.Logf("EXPOSURE %d verdict flips at the %.2f floor", len(verdictFlips), stageTwoMarginFloor)
	t.Logf("EXPOSURE margin direction: %d widened, %d narrowed", marginWidened, marginNarrowed)
	if worstShiftPair != "" {
		t.Logf("EXPOSURE worst margin shift %.6f (%s)", worstShift, worstShiftPair)
	}

	// The margin distribution is reported because the near-boundary count is
	// only meaningful if the generator actually produced pairs near the
	// boundary. A sweep that clusters far from the floor would give a
	// reassuring zero for the wrong reason, so the spread is stated rather than
	// assumed.
	buckets := []struct {
		label     string
		low, high float64
	}{
		{"below 0.00", -2.0, 0.0},
		{"0.00-0.05", 0.0, 0.05},
		{"0.05-0.15", 0.05, stageTwoMarginFloor},
		{"0.15-0.25", stageTwoMarginFloor, 0.25},
		{"0.25-0.50", 0.25, 0.50},
		{"0.50+", 0.50, 2.0},
	}
	counts := make([]int, len(buckets))
	for _, p := range pairs {
		ref := reference[p.id]
		margin := float64(ref.good - ref.bad)
		for i, b := range buckets {
			if margin >= b.low && margin < b.high {
				counts[i]++
				break
			}
		}
	}
	for i, b := range buckets {
		t.Logf("EXPOSURE margin %-10s %3d pairs", b.label, counts[i])
	}

	for _, id := range scoreDiverged {
		t.Logf("EXPOSURE diverging pair %s: interpreter good %.6f bad %.6f, compiler good %.6f bad %.6f",
			id, reference[id].good, reference[id].bad, compiled[id].good, compiled[id].bad)
	}

	if len(verdictFlips) > maxEngineVerdictFlips {
		t.Errorf("%d generated pairs change their Stage 2 margin verdict depending on "+
			"which wazero engine runs the module, want at most %d. A pass/fail that "+
			"depends on the validator's CPU is not a property of the module.",
			len(verdictFlips), maxEngineVerdictFlips)
	}
}

// The generated corpus turned out to measure something the engine question did
// not ask about. Of 375 near-miss-shaped pairs, 66 rank the wrong answer above
// the correct one, which is a far larger effect than the 2-input engine
// divergence and comes from the scorer itself rather than the runtime.
//
// This is the scoreboard for the entity-binding defect, and it has already
// earned its place. The first run measured 66 inversions of 375, with
// identity_binary failing 45 of 45: every single pair whose ground truth restates
// its question ranked the wrong answer first. Closing that hole in
// question_entity_substituted took the total to 21 and identity_binary to 3,
// which are the numbers recorded below.
//
// It also caught two wrong turns that the hand-written pools missed entirely. An
// ungated version of the rule broke seven native tests. A version using the
// ordinary clause boundary instead of the strong one read the comma in "Yes, K2
// is the tallest mountain on Earth." as a sentence start, hid the substituted
// subject, and put identity_binary back to 42 while every other suite stayed
// green. A third gated on the assessment being `None`, which silently missed 9
// of the 45, because a long ground truth yields an acronym candidate and an
// acronym alone is enough to return `Some` while still saying nothing about which
// entity was bound.
//
// It caught a defect in this file too. Brazil originally carried the same string
// for attr and rivalAttr, so its attribute_swap wrong answer was the ground truth
// verbatim and scored 1.0, and three inversions were charged to the scorer that
// belonged to the table.
//
// A later pass took it to 9, all in attribute_swap, by penalising an inserted
// ordinal ("the second tallest") and by stopping a capitalised weather synonym
// from routing a general-knowledge question down the weather path.
//
// The 9 that remain are deliberately not fixed. They substitute one superlative
// for another: "the longest river in Africa" becomes "the deepest river in
// Africa". That is the same surface operation as a correct paraphrase, where
// "the tallest mountain" becomes "the highest peak", and separating the two needs
// a synonym lexicon this module does not have. A heuristic here would penalise
// exactly the paraphrases Telegraph's fixture category rewards, so the ceiling
// records the defect instead of trading a measured loss for an unmeasured one.
// The ceilings are measured, not chosen.
const (
	maxInvertedGeneratedPairs   = 9
	maxBelowFloorGeneratedPairs = 9
)

func TestGeneratedPairRankingQuality(t *testing.T) {
	pairs := generateBoundaryPairs()
	scores := scorePairsWithEngine(t, engineInterpreter, pairs)

	byTemplate := map[string]*struct{ total, inverted, belowFloor int }{}
	order := []string{}
	inverted, belowFloor := 0, 0
	var invertedIDs []string

	for _, p := range pairs {
		family := p.id
		if i := indexByte(family, '/'); i >= 0 {
			family = family[:i]
		}
		stat, seen := byTemplate[family]
		if !seen {
			stat = &struct{ total, inverted, belowFloor int }{}
			byTemplate[family] = stat
			order = append(order, family)
		}

		s := scores[p.id]
		margin := float64(s.good - s.bad)
		stat.total++
		if margin < 0 {
			stat.inverted++
			inverted++
			invertedIDs = append(invertedIDs, p.id)
		}
		if margin < stageTwoMarginFloor {
			stat.belowFloor++
			belowFloor++
		}
	}

	for _, family := range order {
		stat := byTemplate[family]
		t.Logf("QUALITY %-24s %3d pairs, %3d inverted, %3d below the %.2f floor",
			family, stat.total, stat.inverted, stat.belowFloor, stageTwoMarginFloor)
	}
	t.Logf("QUALITY TOTAL %d pairs, %d inverted (%.1f%%), %d below the %.2f floor (%.1f%%)",
		len(pairs), inverted, 100*float64(inverted)/float64(len(pairs)),
		belowFloor, stageTwoMarginFloor, 100*float64(belowFloor)/float64(len(pairs)))

	// Named rather than counted, because a bare total invites the assumption that
	// the remainder is the same defect as the part already fixed. It is not: what
	// survives here swaps the attribute rather than the entity, so it needs a
	// different fix and should be visible as its own list.
	sort.Strings(invertedIDs)
	for _, id := range invertedIDs {
		s := scores[id]
		t.Logf("QUALITY inverted %s: good %.6f, bad %.6f", id, s.good, s.bad)
	}

	if inverted > maxInvertedGeneratedPairs {
		t.Errorf("%d generated pairs rank the wrong answer above the correct one, "+
			"want at most %d", inverted, maxInvertedGeneratedPairs)
	}
	if belowFloor > maxBelowFloorGeneratedPairs {
		t.Errorf("%d generated pairs fall below the %.2f margin floor, want at most %d",
			belowFloor, stageTwoMarginFloor, maxBelowFloorGeneratedPairs)
	}
}

func indexByte(s string, b byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == b {
			return i
		}
	}
	return -1
}
