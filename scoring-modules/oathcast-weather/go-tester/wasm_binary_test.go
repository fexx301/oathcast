package main

import "testing"

func TestExportNamesRejectsUntrustedExportCountWithoutPreallocating(t *testing.T) {
	wasm := []byte{
		0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
		0x07, 0x05,
		0xff, 0xff, 0xff, 0xff, 0x0f,
	}

	if _, err := exportNames(wasm); err == nil {
		t.Fatal("expected the truncated export section to be rejected")
	}
}
