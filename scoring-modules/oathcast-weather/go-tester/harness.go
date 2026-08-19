package main

import (
	"context"
	"fmt"
	"math"
	"os"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/api"
)

type scorerModule struct {
	ctx     context.Context
	runtime wazero.Runtime
	module  api.Module
	memory  api.Memory
	alloc   api.Function
	dealloc api.Function
	rank    api.Function
}

// wasmEngine selects which of wazero's two execution engines runs the module.
// The choice is not a performance detail here: the same module bytes have been
// measured producing different scores under the two engines on linux/amd64, so
// any number quoted about the scorer has to say which engine produced it. See
// TestEngineScoresAgree for the measured divergence.
type wasmEngine int

const (
	// engineCompiler is wazero's per-architecture optimising compiler. It is
	// wazero's default, so it is what a validator gets without asking, and it
	// is what CI exercises everywhere except this one benchmark.
	engineCompiler wasmEngine = iota
	// engineInterpreter is wazero's portable interpreter. Of the six
	// os/arch/engine combinations measured, five agree and all of those five
	// match the interpreter, so it is used as the reference for measurements
	// that are meant to describe the module rather than the host.
	engineInterpreter
)

func (e wasmEngine) String() string {
	if e == engineInterpreter {
		return "interpreter"
	}
	return "compiler"
}

func openScorer(path string) (*scorerModule, error) {
	return openScorerWithEngine(path, engineCompiler)
}

func openScorerWithEngine(path string, engine wasmEngine) (*scorerModule, error) {
	wasmBytes, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read WASM: %w", err)
	}
	ctx := context.Background()
	config := wazero.NewRuntimeConfigCompiler()
	if engine == engineInterpreter {
		config = wazero.NewRuntimeConfigInterpreter()
	}
	runtime := wazero.NewRuntimeWithConfig(ctx, config)
	module, err := runtime.Instantiate(ctx, wasmBytes)
	if err != nil {
		runtime.Close(ctx)
		return nil, fmt.Errorf("instantiate WASM: %w", err)
	}
	memory := module.Memory()
	alloc := module.ExportedFunction("alloc")
	dealloc := module.ExportedFunction("dealloc")
	rank := module.ExportedFunction("rank_answer")
	if memory == nil || alloc == nil || dealloc == nil || rank == nil {
		module.Close(ctx)
		runtime.Close(ctx)
		return nil, fmt.Errorf("missing documented memory or function export")
	}
	return &scorerModule{
		ctx: ctx, runtime: runtime, module: module, memory: memory,
		alloc: alloc, dealloc: dealloc, rank: rank,
	}, nil
}

func (s *scorerModule) close() {
	s.module.Close(s.ctx)
	s.runtime.Close(s.ctx)
}

func (s *scorerModule) writeBytes(value []byte) (uint32, uint32, error) {
	if len(value) == 0 {
		return 0, 0, nil
	}
	result, err := s.alloc.Call(s.ctx, uint64(len(value)))
	if err != nil {
		return 0, 0, fmt.Errorf("alloc %d bytes: %w", len(value), err)
	}
	if len(result) != 1 {
		return 0, 0, fmt.Errorf("alloc returned %d results", len(result))
	}
	pointer := uint32(result[0])
	if pointer == 0 {
		return 0, 0, fmt.Errorf("alloc returned null for %d bytes", len(value))
	}
	if !s.memory.Write(pointer, value) {
		return 0, 0, fmt.Errorf("memory write failed at %d for %d bytes", pointer, len(value))
	}
	return pointer, uint32(len(value)), nil
}

func (s *scorerModule) scoreBytes(question, groundTruth, minerAnswer []byte) (float32, error) {
	qPtr, qLen, err := s.writeBytes(question)
	if err != nil {
		return 0, err
	}
	gtPtr, gtLen, err := s.writeBytes(groundTruth)
	if err != nil {
		return 0, err
	}
	maPtr, maLen, err := s.writeBytes(minerAnswer)
	if err != nil {
		return 0, err
	}
	result, err := s.rank.Call(
		s.ctx,
		uint64(qPtr), uint64(qLen),
		uint64(gtPtr), uint64(gtLen),
		uint64(maPtr), uint64(maLen),
	)
	if err != nil {
		return 0, fmt.Errorf("rank_answer: %w", err)
	}
	if len(result) != 1 {
		return 0, fmt.Errorf("rank_answer returned %d results", len(result))
	}
	score := api.DecodeF32(result[0])
	if math.IsNaN(float64(score)) || math.IsInf(float64(score), 0) || score < 0 || score > 1 {
		return 0, fmt.Errorf("invalid score %v", score)
	}
	return score, nil
}

func (s *scorerModule) score(question, groundTruth, minerAnswer string) (float32, error) {
	return s.scoreBytes([]byte(question), []byte(groundTruth), []byte(minerAnswer))
}
