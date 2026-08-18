package main

import "fmt"

type wasmSection struct {
	id      byte
	payload []byte
}

func readName(data []byte, cursor *int) (string, error) {
	length, err := readULEB(data, cursor)
	if err != nil {
		return "", err
	}
	end := *cursor + int(length)
	if end < *cursor || end > len(data) {
		return "", fmt.Errorf("name is out of bounds")
	}
	name := string(data[*cursor:end])
	*cursor = end
	return name, nil
}

func readULEB(data []byte, cursor *int) (uint64, error) {
	var value uint64
	var shift uint
	for {
		if *cursor >= len(data) || shift >= 64 {
			return 0, fmt.Errorf("invalid unsigned LEB128")
		}
		current := data[*cursor]
		*cursor++
		value |= uint64(current&0x7f) << shift
		if current&0x80 == 0 {
			return value, nil
		}
		shift += 7
	}
}

func parseSections(wasm []byte) ([]wasmSection, error) {
	if len(wasm) < 8 || string(wasm[:4]) != "\x00asm" || string(wasm[4:8]) != "\x01\x00\x00\x00" {
		return nil, fmt.Errorf("invalid WASM header")
	}
	var sections []wasmSection
	cursor := 8
	for cursor < len(wasm) {
		id := wasm[cursor]
		cursor++
		size, err := readULEB(wasm, &cursor)
		if err != nil {
			return nil, err
		}
		end := cursor + int(size)
		if end < cursor || end > len(wasm) {
			return nil, fmt.Errorf("section %d is out of bounds", id)
		}
		sections = append(sections, wasmSection{id: id, payload: wasm[cursor:end]})
		cursor = end
	}
	return sections, nil
}

func hasSection(wasm []byte, id byte) (bool, error) {
	sections, err := parseSections(wasm)
	if err != nil {
		return false, err
	}
	for _, section := range sections {
		if section.id == id {
			return true, nil
		}
	}
	return false, nil
}

func exportNames(wasm []byte) ([]string, error) {
	sections, err := parseSections(wasm)
	if err != nil {
		return nil, err
	}
	for _, section := range sections {
		if section.id != 7 {
			continue
		}
		cursor := 0
		count, err := readULEB(section.payload, &cursor)
		if err != nil {
			return nil, err
		}
		// The count is attacker-controlled ULEB128 data. Bounds checks below
		// constrain parsing, so do not also trust it as an allocation size.
		names := make([]string, 0)
		for index := uint64(0); index < count; index++ {
			name, err := readName(section.payload, &cursor)
			if err != nil {
				return nil, err
			}
			if cursor >= len(section.payload) {
				return nil, fmt.Errorf("export %q is missing its kind", name)
			}
			cursor++ // external kind
			if _, err := readULEB(section.payload, &cursor); err != nil {
				return nil, err
			}
			names = append(names, name)
		}
		if cursor != len(section.payload) {
			return nil, fmt.Errorf("trailing bytes in export section")
		}
		return names, nil
	}
	return nil, fmt.Errorf("WASM has no export section")
}
