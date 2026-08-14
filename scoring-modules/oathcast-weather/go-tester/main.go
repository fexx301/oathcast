package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) != 5 {
		fmt.Println("usage: go run . <path-to.wasm> <question> <ground_truth> <miner_answer>")
		os.Exit(1)
	}
	scorer, err := openScorer(os.Args[1])
	if err != nil {
		panic(err)
	}
	defer scorer.close()
	score, err := scorer.score(os.Args[2], os.Args[3], os.Args[4])
	if err != nil {
		panic(err)
	}
	fmt.Printf("score: %.4f\n", score)
}
