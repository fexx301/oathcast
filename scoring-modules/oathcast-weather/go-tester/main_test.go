package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
)

const (
	maxWASMBytes        = 32 * 1024 * 1024
	maxLinearMemoryPage = 64
	maxQuestionBytes    = 8 * 1024
	maxGroundTruthBytes = 8 * 1024
	maxMinerAnswerBytes = 4 * 1024
)

func repoRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func wasmPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(
		repoRoot(t), "scoring-modules", "oathcast-weather", "rust-module", "target",
		"wasm32-unknown-unknown", "release", "oathcast_weather_scorer.wasm",
	)
}

func assertSignature(t *testing.T, definition api.FunctionDefinition, params, results []api.ValueType) {
	t.Helper()
	if !sameTypes(definition.ParamTypes(), params) || !sameTypes(definition.ResultTypes(), results) {
		t.Fatalf(
			"unexpected signature for %s: params=%v results=%v",
			definition.DebugName(), definition.ParamTypes(), definition.ResultTypes(),
		)
	}
}

func sameTypes(left, right []api.ValueType) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func TestArtifactContract(t *testing.T) {
	bytes, err := os.ReadFile(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	evidence := loadReleaseEvidence(t)
	if len(bytes) != evidence.Artifact.ByteSize {
		t.Fatalf(
			"WASM is %d bytes, release evidence pins %d",
			len(bytes), evidence.Artifact.ByteSize,
		)
	}
	digest := sha256.Sum256(bytes)
	if actual := hex.EncodeToString(digest[:]); actual != evidence.Artifact.SHA256 {
		t.Fatalf("WASM SHA-256 is %s, release evidence pins %s", actual, evidence.Artifact.SHA256)
	}
	if len(bytes) > maxWASMBytes {
		t.Fatalf("WASM is %d bytes, above the 32 MiB limit", len(bytes))
	}
	ctx := context.Background()
	runtime := wazero.NewRuntime(ctx)
	defer runtime.Close(ctx)
	compiled, err := runtime.CompileModule(ctx, bytes)
	if err != nil {
		t.Fatal(err)
	}
	defer compiled.Close(ctx)

	if imports := compiled.ImportedFunctions(); len(imports) != 0 {
		t.Fatalf("unexpected function imports: %v", imports)
	}
	if imports := compiled.ImportedMemories(); len(imports) != 0 {
		t.Fatalf("unexpected memory imports: %v", imports)
	}
	if hasImportSection, err := hasSection(bytes, 2); err != nil {
		t.Fatal(err)
	} else if hasImportSection {
		t.Fatal("WASM must not contain an import section")
	}
	exports := compiled.ExportedFunctions()
	if len(exports) != 3 {
		t.Fatalf("expected exactly three function exports, got %v", exports)
	}
	i32 := api.ValueTypeI32
	f32 := api.ValueTypeF32
	assertSignature(t, exports["alloc"], []api.ValueType{i32}, []api.ValueType{i32})
	assertSignature(t, exports["dealloc"], []api.ValueType{i32, i32}, nil)
	assertSignature(
		t, exports["rank_answer"],
		[]api.ValueType{i32, i32, i32, i32, i32, i32},
		[]api.ValueType{f32},
	)
	memories := compiled.ExportedMemories()
	if len(memories) != 1 || memories["memory"] == nil {
		t.Fatalf("expected exactly one exported linear memory named memory, got %v", memories)
	}
	if maxPages, hasMax := memories["memory"].Max(); !hasMax || maxPages != maxLinearMemoryPage {
		t.Fatalf(
			"exported memory maximum is pages=%d declared=%v, want exactly %d pages (4 MiB)",
			maxPages, hasMax, maxLinearMemoryPage,
		)
	}
	exportNames, err := exportNames(bytes)
	if err != nil {
		t.Fatal(err)
	}
	if len(exportNames) != 6 {
		t.Fatalf("expected documented ABI plus two Rust linker globals, got %v", exportNames)
	}
	wantedExports := map[string]bool{
		"memory": true, "alloc": true, "dealloc": true, "rank_answer": true,
		"__data_end": true, "__heap_base": true,
	}
	for _, name := range exportNames {
		if !wantedExports[name] {
			t.Fatalf("unexpected export %q in %v", name, exportNames)
		}
	}
	if hasStart, err := hasSection(bytes, 8); err != nil {
		t.Fatal(err)
	} else if hasStart {
		t.Fatal("WASM must not contain a start section")
	}
}

type scoreExpectation struct {
	Exact *float32 `json:"exact"`
	Min   *float32 `json:"min"`
	Max   *float32 `json:"max"`
}

type repeatValue struct {
	Value string `json:"value"`
	Count int    `json:"count"`
}

type fixtureCase struct {
	CaseID        string           `json:"case_id"`
	Question      string           `json:"question"`
	GroundTruth   string           `json:"ground_truth"`
	MinerAnswer   *string          `json:"miner_answer"`
	MinerRepeat   *repeatValue     `json:"miner_answer_repeat"`
	Expected      scoreExpectation `json:"expected_score"`
	OrderingGroup string           `json:"ordering_group"`
	QualityRank   int              `json:"quality_rank"`
}

type fixtureFile struct {
	Cases []fixtureCase `json:"cases"`
}

type releaseEvidence struct {
	Artifact struct {
		ByteSize int    `json:"byte_size"`
		SHA256   string `json:"sha256"`
	} `json:"artifact"`
}

func loadReleaseEvidence(t *testing.T) releaseEvidence {
	t.Helper()
	bytes, err := os.ReadFile(filepath.Join(
		repoRoot(t), "scoring-modules", "oathcast-weather", "release-evidence.json",
	))
	if err != nil {
		t.Fatal(err)
	}
	var evidence releaseEvidence
	if err := json.Unmarshal(bytes, &evidence); err != nil {
		t.Fatal(err)
	}
	return evidence
}

func loadFixture(t *testing.T) fixtureFile {
	t.Helper()
	bytes, err := os.ReadFile(filepath.Join(repoRoot(t), "fixtures", "wasm_scoring_cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture fixtureFile
	if err := json.Unmarshal(bytes, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func (c fixtureCase) answer(t *testing.T) string {
	t.Helper()
	if c.MinerAnswer != nil {
		return *c.MinerAnswer
	}
	if c.MinerRepeat == nil || c.MinerRepeat.Count < 0 || c.MinerRepeat.Count > 100000 {
		t.Fatalf("invalid answer materialization for %s", c.CaseID)
	}
	return strings.Repeat(c.MinerRepeat.Value, c.MinerRepeat.Count)
}

func TestFixtureCorpus(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	fixture := loadFixture(t)
	type orderedScore struct {
		rank  int
		score float32
	}
	groups := map[string][]orderedScore{}

	for _, testCase := range fixture.Cases {
		t.Run(testCase.CaseID, func(t *testing.T) {
			score, err := scorer.score(testCase.Question, testCase.GroundTruth, testCase.answer(t))
			if err != nil {
				t.Fatal(err)
			}
			if testCase.Expected.Exact != nil && score != *testCase.Expected.Exact {
				t.Fatalf("score=%v, want exactly %v", score, *testCase.Expected.Exact)
			}
			if testCase.Expected.Min != nil && score+1e-6 < *testCase.Expected.Min {
				t.Fatalf("score=%v, want >= %v", score, *testCase.Expected.Min)
			}
			if testCase.Expected.Max != nil && score-1e-6 > *testCase.Expected.Max {
				t.Fatalf("score=%v, want <= %v", score, *testCase.Expected.Max)
			}
			if testCase.OrderingGroup != "" {
				groups[testCase.OrderingGroup] = append(
					groups[testCase.OrderingGroup], orderedScore{rank: testCase.QualityRank, score: score},
				)
			}
		})
	}
	for group, scores := range groups {
		for _, lower := range scores {
			for _, higher := range scores {
				if higher.rank > lower.rank && higher.score <= lower.score+1e-6 {
					t.Errorf(
						"ordering %s failed: rank %d score %.6f must beat rank %d score %.6f",
						group, higher.rank, higher.score, lower.rank, lower.score,
					)
				}
			}
		}
	}
}

func TestRepeatedCallsAreDeterministicAndResetAllocator(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	var first float32
	for index := 0; index < 5000; index++ {
		score, err := scorer.score("Will it rain?", "Yes. Rain will occur.", "Yes. Rain is likely.")
		if err != nil {
			t.Fatalf("call %d: %v", index, err)
		}
		if index == 0 {
			first = score
		} else if math.Float32bits(score) != math.Float32bits(first) {
			t.Fatalf("call %d returned %.9f, first was %.9f", index, score, first)
		}
	}
}

func TestMalformedUTF8AndOutOfBoundsArgumentsReturnZero(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()
	score, err := scorer.scoreBytes([]byte("question"), []byte("truth"), []byte{0xff, 0xfe})
	if err != nil {
		t.Fatal(err)
	}
	if score != 0 {
		t.Fatalf("invalid UTF-8 score=%v, want 0", score)
	}

	result, err := scorer.rank.Call(scorer.ctx, 1, 4, 1, 4, 1, 4)
	if err != nil {
		t.Fatal(err)
	}
	if api.DecodeF32(result[0]) != 0 {
		t.Fatal("unallocated pointers must score zero")
	}
}

func TestOversizedAllocationTrapsWithoutCorruptingNewInstance(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	_, err = scorer.alloc.Call(scorer.ctx, 2*1024*1024)
	if err == nil {
		t.Fatal("oversized allocation should trap")
	}
	scorer.close()

	fresh, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer fresh.close()
	if score, err := fresh.score("q", "rain", "rain"); err != nil || score != 1 {
		t.Fatalf("fresh instance score=%v err=%v", score, err)
	}
}

func TestInputByteBoundaries(t *testing.T) {
	tests := []struct {
		name        string
		question    string
		truth       string
		answer      string
		wantAtCap   float32
		wantOverCap float32
	}{
		{
			name:        "miner answer",
			question:    "q",
			truth:       strings.Repeat("a", maxMinerAnswerBytes),
			answer:      strings.Repeat("a", maxMinerAnswerBytes),
			wantAtCap:   1,
			wantOverCap: 0,
		},
		{
			name:        "question",
			question:    strings.Repeat("q", maxQuestionBytes),
			truth:       "rain",
			answer:      "rain",
			wantAtCap:   1,
			wantOverCap: 0,
		},
		{
			name:        "ground truth",
			question:    "q",
			truth:       "rain" + strings.Repeat(" ", maxGroundTruthBytes-len("rain")),
			answer:      "rain",
			wantAtCap:   1,
			wantOverCap: 0,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			scorer, err := openScorer(wasmPath(t))
			if err != nil {
				t.Fatal(err)
			}
			defer scorer.close()

			atCap, err := scorer.score(testCase.question, testCase.truth, testCase.answer)
			if err != nil {
				t.Fatalf("at-cap call: %v", err)
			}
			if atCap != testCase.wantAtCap {
				t.Fatalf("at-cap score=%v, want %v", atCap, testCase.wantAtCap)
			}

			question, truth, answer := testCase.question, testCase.truth, testCase.answer
			switch testCase.name {
			case "miner answer":
				answer += "a"
			case "question":
				question += "q"
			case "ground truth":
				truth += " "
			}
			overCap, err := scorer.score(question, truth, answer)
			if err != nil {
				t.Fatalf("over-cap call: %v", err)
			}
			if overCap != testCase.wantOverCap {
				t.Fatalf("over-cap score=%v, want %v", overCap, testCase.wantOverCap)
			}
		})
	}
}

func TestDeallocRequiresExactLivePair(t *testing.T) {
	t.Run("exact pair invalidates allocation", func(t *testing.T) {
		scorer, err := openScorer(wasmPath(t))
		if err != nil {
			t.Fatal(err)
		}
		defer scorer.close()
		qPtr, qLen, err := scorer.writeBytes([]byte("q"))
		if err != nil {
			t.Fatal(err)
		}
		gtPtr, gtLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		answerPtr, answerLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := scorer.dealloc.Call(scorer.ctx, uint64(answerPtr), uint64(answerLen)); err != nil {
			t.Fatal(err)
		}
		result, err := scorer.rank.Call(
			scorer.ctx,
			uint64(qPtr), uint64(qLen),
			uint64(gtPtr), uint64(gtLen),
			uint64(answerPtr), uint64(answerLen),
		)
		if err != nil {
			t.Fatal(err)
		}
		if score := api.DecodeF32(result[0]); score != 0 {
			t.Fatalf("deallocated answer score=%v, want 0", score)
		}
	})

	t.Run("mismatched pair is ignored", func(t *testing.T) {
		scorer, err := openScorer(wasmPath(t))
		if err != nil {
			t.Fatal(err)
		}
		defer scorer.close()
		qPtr, qLen, err := scorer.writeBytes([]byte("q"))
		if err != nil {
			t.Fatal(err)
		}
		gtPtr, gtLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		answerPtr, answerLen, err := scorer.writeBytes([]byte("rain"))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := scorer.dealloc.Call(scorer.ctx, uint64(answerPtr), uint64(answerLen-1)); err != nil {
			t.Fatal(err)
		}
		result, err := scorer.rank.Call(
			scorer.ctx,
			uint64(qPtr), uint64(qLen),
			uint64(gtPtr), uint64(gtLen),
			uint64(answerPtr), uint64(answerLen),
		)
		if err != nil {
			t.Fatal(err)
		}
		if score := api.DecodeF32(result[0]); score != 1 {
			t.Fatalf("mismatched dealloc score=%v, want 1", score)
		}
	})
}

func TestJSONEscapingIsValidatedAndScored(t *testing.T) {
	scorer, err := openScorer(wasmPath(t))
	if err != nil {
		t.Fatal(err)
	}
	defer scorer.close()

	t.Run("valid escaped content", func(t *testing.T) {
		score, err := scorer.score(
			"q",
			"Rain likely.",
			`{"content":"Rain \"likely\".","metadata":"line one\nline two","probability":0.65}`,
		)
		if err != nil {
			t.Fatal(err)
		}
		if score != 1 {
			t.Fatalf("escaped JSON content score=%v, want 1", score)
		}
	})

	t.Run("invalid escape is rejected", func(t *testing.T) {
		score, err := scorer.score("q", "rain", `{"content":"rain\q"}`)
		if err != nil {
			t.Fatal(err)
		}
		if score != 0 {
			t.Fatalf("invalid JSON escape score=%v, want 0", score)
		}
	})
}
