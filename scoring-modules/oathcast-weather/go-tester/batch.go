package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/tetratelabs/wazero"
)

// batchCase is one JSON line on stdin. id is echoed back so callers can join
// scores to their own records; it never reaches the module.
type batchCase struct {
	ID          string `json:"id"`
	Question    string `json:"question"`
	GroundTruth string `json:"ground_truth"`
	MinerAnswer string `json:"miner_answer"`
}

// compiledCache holds one runtime and one compiled module and hands out fresh
// instances of it.
type compiledCache struct {
	ctx      context.Context
	runtime  wazero.Runtime
	compiled wazero.CompiledModule
	err      error
}

func newCompiledCache(wasmBytes []byte, engine wasmEngine) *compiledCache {
	ctx := context.Background()
	config := wazero.NewRuntimeConfigCompiler()
	if engine == engineInterpreter {
		config = wazero.NewRuntimeConfigInterpreter()
	}
	runtime := wazero.NewRuntimeWithConfig(ctx, config)
	compiled, err := runtime.CompileModule(ctx, wasmBytes)
	return &compiledCache{ctx: ctx, runtime: runtime, compiled: compiled, err: err}
}

func (c *compiledCache) instantiate() (*scorerModule, error) {
	if c.err != nil {
		return nil, fmt.Errorf("compile WASM: %w", c.err)
	}
	module, err := c.runtime.InstantiateModule(c.ctx, c.compiled, wazero.NewModuleConfig().WithName(""))
	if err != nil {
		return nil, fmt.Errorf("instantiate WASM: %w", err)
	}
	memory := module.Memory()
	alloc := module.ExportedFunction("alloc")
	dealloc := module.ExportedFunction("dealloc")
	rank := module.ExportedFunction("rank_answer")
	if memory == nil || alloc == nil || dealloc == nil || rank == nil {
		module.Close(c.ctx)
		return nil, fmt.Errorf("missing documented memory or function export")
	}
	return &scorerModule{
		ctx: c.ctx, runtime: c.runtime, module: module, memory: memory,
		alloc: alloc, dealloc: dealloc, rank: rank,
	}, nil
}

func (c *compiledCache) close() {
	c.runtime.Close(c.ctx)
}

type batchResult struct {
	ID     string  `json:"id"`
	Score  float32 `json:"score"`
	Engine string  `json:"engine"`
	Error  string  `json:"error,omitempty"`
}

// runBatch scores every JSON line on in with one instantiated module, so a
// large module (the MiniLM baseline is ~24 MB) is compiled once rather than
// once per triple. A fresh module is instantiated for each case: the baseline
// does not free its inputs, and reusing one instance would let earlier cases
// change the memory later cases are scored in.
func runBatch(wasmPath string, engine wasmEngine, in io.Reader, out io.Writer) error {
	wasmBytes, err := os.ReadFile(wasmPath)
	if err != nil {
		return fmt.Errorf("read WASM: %w", err)
	}
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 1<<20), 1<<24)
	encoder := json.NewEncoder(out)
	cache := newCompiledCache(wasmBytes, engine)
	defer cache.close()
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var c batchCase
		if err := json.Unmarshal(line, &c); err != nil {
			return fmt.Errorf("decode case: %w", err)
		}
		result := batchResult{ID: c.ID, Engine: engine.String()}
		scorer, err := cache.instantiate()
		if err != nil {
			return err
		}
		score, err := scorer.score(c.Question, c.GroundTruth, c.MinerAnswer)
		scorer.module.Close(scorer.ctx)
		if err != nil {
			result.Error = err.Error()
		} else {
			result.Score = score
		}
		if err := encoder.Encode(result); err != nil {
			return err
		}
	}
	return scanner.Err()
}
