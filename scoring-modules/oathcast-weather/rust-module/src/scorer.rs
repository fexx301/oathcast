use crate::text::{
    TokenIter, has_time_outside_question, is_json_like, is_valid_json, parse_decimal, probability,
    select_scoring_text, semantic_hash, token_eq, weather_concept_mask,
};

pub(crate) const MAX_QUESTION_BYTES: usize = 8 * 1024;
pub(crate) const MAX_GROUND_TRUTH_BYTES: usize = 8 * 1024;
pub(crate) const MAX_MINER_ANSWER_BYTES: usize = 4 * 1024;

const MAX_TRACKED_TOKENS: usize = 512;
const MAX_SEMANTIC_TOKENS: usize = 384;
const MAX_NUMERIC_FACTS: usize = 64;
const MAX_FACT_ANCHORS: usize = 96;
const MAX_FACT_RELATION_PAIRS: usize = 192;
const FACT_RELATION_WINDOW: usize = 6;

pub(crate) const ISSUE_EMPTY_ANSWER: u32 = 1 << 0;
pub(crate) const ISSUE_INPUT_TOO_LONG: u32 = 1 << 1;
pub(crate) const ISSUE_EMPTY_GROUND_TRUTH: u32 = 1 << 2;
pub(crate) const ISSUE_MALFORMED_JSON: u32 = 1 << 3;
pub(crate) const ISSUE_WRONG_TIME_WINDOW: u32 = 1 << 4;
pub(crate) const ISSUE_KEYWORD_STUFFING: u32 = 1 << 5;
pub(crate) const ISSUE_CONTRADICTORY_POLARITY: u32 = 1 << 6;
pub(crate) const ISSUE_CONTRADICTORY_PROBABILITY: u32 = 1 << 7;
pub(crate) const ISSUE_AMBIGUOUS_GROUND_TRUTH: u32 = 1 << 8;
pub(crate) const ISSUE_POLARITY_MISMATCH: u32 = 1 << 9;
pub(crate) const ISSUE_NO_SCORABLE_GROUND_TRUTH: u32 = 1 << 10;
pub(crate) const ISSUE_MISSING_BINARY_ANSWER: u32 = 1 << 11;
pub(crate) const ISSUE_CONTRADICTORY_FACT_ANCHOR: u32 = 1 << 12;
pub(crate) const ISSUE_AMBIGUOUS_FACT_ANCHORS: u32 = 1 << 13;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Evaluation {
    pub score: f32,
    pub issues: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Polarity {
    Unknown,
    Positive,
    Negative,
    Contradictory,
}

struct HashSet<const N: usize> {
    values: [u64; N],
    len: usize,
}

impl<const N: usize> HashSet<N> {
    fn new() -> Self {
        Self {
            values: [0; N],
            len: 0,
        }
    }

    fn insert(&mut self, value: u64) {
        if self.contains(value) || self.len == N {
            return;
        }
        self.values[self.len] = value;
        self.len += 1;
    }

    fn contains(&self, value: u64) -> bool {
        self.values[..self.len].contains(&value)
    }

    fn overlap(&self, other: &Self) -> usize {
        self.values[..self.len]
            .iter()
            .filter(|value| other.contains(**value))
            .count()
    }
}

fn zero(issues: u32) -> Evaluation {
    Evaluation { score: 0.0, issues }
}

fn is_negative_contraction(text: &str, token: crate::text::Token<'_>) -> bool {
    let stem = token.bytes;
    let is_stem = token_eq(stem, b"won")
        || token_eq(stem, b"didn")
        || token_eq(stem, b"can")
        || token_eq(stem, b"isn")
        || token_eq(stem, b"wasn")
        || token_eq(stem, b"doesn")
        || token_eq(stem, b"don")
        || token_eq(stem, b"shouldn")
        || token_eq(stem, b"wouldn")
        || token_eq(stem, b"couldn");
    if !is_stem {
        return false;
    }
    let suffix = text.as_bytes().get(token.end..).unwrap_or_default();
    suffix.starts_with(b"'t") || suffix.starts_with(&[0xe2, 0x80, 0x99, b't'])
}

fn explicit_polarity(text: &str) -> Polarity {
    let mut negative = false;
    let mut explicit_positive = false;
    let mut inferred_positive = false;
    let mut previous: Option<&[u8]> = None;
    let mut negation_scope: u8 = 0;

    for token in TokenIter::new(text) {
        let current = token.bytes;

        if token_eq(current, b"but")
            || token_eq(current, b"however")
            || token_eq(current, b"yet")
            || token_eq(current, b"although")
        {
            negation_scope = 0;
            previous = Some(current);
            continue;
        }

        if token_eq(current, b"no") || token_eq(current, b"false") {
            negative = true;
            negation_scope = 6;
        } else if token_eq(current, b"not")
            || token_eq(current, b"never")
            || token_eq(current, b"cannot")
            || is_negative_contraction(text, token)
        {
            negation_scope = 6;
        } else if token_eq(current, b"unlikely") {
            negative = true;
        }

        if token_eq(current, b"yes") || token_eq(current, b"true") {
            if negation_scope == 0 {
                explicit_positive = true;
            } else {
                negative = true;
            }
        } else if token_eq(current, b"likely")
            || token_eq(current, b"expected")
            || token_eq(current, b"occurred")
            || (token_eq(current, b"occur")
                && (negation_scope > 0 || previous.is_some_and(|value| token_eq(value, b"will"))))
        {
            if negation_scope > 0 {
                negative = true;
            } else {
                inferred_positive = true;
            }
        }

        negation_scope = negation_scope.saturating_sub(1);
        previous = Some(current);
    }

    if negative && explicit_positive {
        Polarity::Contradictory
    } else if negative && inferred_positive {
        Polarity::Unknown
    } else if negative {
        Polarity::Negative
    } else if explicit_positive || inferred_positive {
        Polarity::Positive
    } else {
        Polarity::Unknown
    }
}

fn stuffing_stats(text: &str) -> (usize, usize, usize) {
    let mut hashes = [0u64; MAX_TRACKED_TOKENS];
    let mut counts = [0u16; MAX_TRACKED_TOKENS];
    let mut tracked = 0usize;
    let mut total = 0usize;
    let mut max_count = 0usize;

    for token in TokenIter::new(text) {
        total += 1;
        let hash = crate::text::hash_lower(token.bytes);
        let mut found = None;
        for (index, value) in hashes[..tracked].iter().enumerate() {
            if *value == hash {
                found = Some(index);
                break;
            }
        }
        if let Some(index) = found {
            counts[index] = counts[index].saturating_add(1);
            max_count = max_count.max(counts[index] as usize);
        } else if tracked < MAX_TRACKED_TOKENS {
            hashes[tracked] = hash;
            counts[tracked] = 1;
            tracked += 1;
            max_count = max_count.max(1);
        }
    }
    (total, tracked, max_count)
}

fn is_keyword_stuffed(text: &str) -> bool {
    let (total, unique, max_count) = stuffing_stats(text);
    total >= 36 && (unique as f32 / total as f32) < 0.45 && max_count >= 6
}

fn semantic_set(text: &str) -> HashSet<MAX_SEMANTIC_TOKENS> {
    let mut set = HashSet::new();
    for token in TokenIter::new(text) {
        if let Some(hash) = semantic_hash(token.bytes) {
            set.insert(hash);
        }
    }
    set
}

fn semantic_f1(ground_truth: &str, answer: &str) -> Option<f32> {
    let truth = semantic_set(ground_truth);
    let response = semantic_set(answer);
    if truth.len == 0 {
        return None;
    }
    if response.len == 0 {
        return Some(0.0);
    }
    let overlap = truth.overlap(&response) as f32;
    let precision = overlap / response.len as f32;
    let recall = overlap / truth.len as f32;
    if precision + recall == 0.0 {
        Some(0.0)
    } else {
        Some((2.0 * precision * recall) / (precision + recall))
    }
}

fn decoded_json_content_eq(raw_content: &str, expected: &str) -> bool {
    let raw = raw_content.as_bytes();
    let expected = expected.as_bytes();
    let mut raw_index = 0usize;
    let mut expected_index = 0usize;

    while raw_index < raw.len() && expected_index < expected.len() {
        let decoded = if raw[raw_index] == b'\\' {
            raw_index += 1;
            match raw.get(raw_index).copied() {
                Some(b'"') => b'"',
                Some(b'\\') => b'\\',
                Some(b'/') => b'/',
                Some(b'b') => 0x08,
                Some(b'f') => 0x0c,
                Some(b'n') => b'\n',
                Some(b'r') => b'\r',
                Some(b't') => b'\t',
                _ => return false,
            }
        } else {
            raw[raw_index]
        };
        if decoded != expected[expected_index] {
            return false;
        }
        raw_index += 1;
        expected_index += 1;
    }
    raw_index == raw.len() && expected_index == expected.len()
}

fn numeric_operator_mask(text: &str) -> u8 {
    let bytes = text.as_bytes();
    let mut mask = 0u8;
    for (index, byte) in bytes.iter().copied().enumerate() {
        if !matches!(byte, b'/' | b'-' | b':') {
            continue;
        }
        let left = bytes[..index]
            .iter()
            .rev()
            .find(|value| !value.is_ascii_whitespace());
        let right = bytes[index + 1..]
            .iter()
            .find(|value| !value.is_ascii_whitespace());
        if !left.is_some_and(|value| value.is_ascii_digit())
            || !right.is_some_and(|value| value.is_ascii_digit())
        {
            continue;
        }
        mask |= match byte {
            b'/' => 1 << 0,
            b'-' => 1 << 1,
            b':' => 1 << 2,
            _ => 0,
        };
    }
    mask
}

fn numeric_binding_conflict(ground_truth: &str, answer: &str) -> bool {
    let truth = numeric_set(ground_truth);
    let response = numeric_set(answer);
    truth.len > 0
        && truth.len == response.len
        && truth.overlap(&response) == truth.len
        && numeric_operator_mask(ground_truth) != numeric_operator_mask(answer)
}

fn is_fact_relation_or_filler(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"actually",
        b"also",
        b"against",
        b"animal",
        b"answer",
        b"are",
        b"as",
        b"artist",
        b"author",
        b"authored",
        b"based",
        b"became",
        b"been",
        b"being",
        b"called",
        b"cannot",
        b"capital",
        b"chemical",
        b"city",
        b"circulate",
        b"circulates",
        b"commonly",
        b"contains",
        b"correct",
        b"created",
        b"creator",
        b"country",
        b"couldn",
        b"currency",
        b"day",
        b"days",
        b"discovered",
        b"didn",
        b"does",
        b"doesn",
        b"don",
        b"expected",
        b"flows",
        b"founded",
        b"founder",
        b"founders",
        b"government",
        b"had",
        b"happened",
        b"happens",
        b"has",
        b"have",
        b"her",
        b"high",
        b"highest",
        b"his",
        b"hour",
        b"hours",
        b"invented",
        b"inventor",
        b"isn",
        b"its",
        b"known",
        b"language",
        b"land",
        b"largest",
        b"learn",
        b"learned",
        b"learns",
        b"level",
        b"located",
        b"lose",
        b"loses",
        b"lost",
        b"maximum",
        b"means",
        b"month",
        b"months",
        b"mount",
        b"named",
        b"national",
        b"never",
        b"not",
        b"ocean",
        b"official",
        b"organ",
        b"overcame",
        b"overcome",
        b"overcomes",
        b"painted",
        b"painter",
        b"peak",
        b"place",
        b"planet",
        b"refers",
        b"remains",
        b"river",
        b"runs",
        b"s",
        b"served",
        b"serves",
        b"shouldn",
        b"situated",
        b"speak",
        b"speaks",
        b"spoken",
        b"symbol",
        b"tallest",
        b"took",
        b"used",
        b"uses",
        b"wasn",
        b"week",
        b"weeks",
        b"weren",
        b"win",
        b"wins",
        b"won",
        b"world",
        b"wouldn",
        b"write",
        b"writes",
        b"wrote",
        b"written",
        b"year",
        b"years",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

fn is_fact_relation_token(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"answer",
        b"author",
        b"authored",
        b"based",
        b"call",
        b"called",
        b"capital",
        b"circulate",
        b"circulates",
        b"contains",
        b"correct",
        b"create",
        b"created",
        b"discover",
        b"discovered",
        b"flow",
        b"flows",
        b"found",
        b"founded",
        b"invent",
        b"invented",
        b"known",
        b"learn",
        b"learned",
        b"learns",
        b"locate",
        b"located",
        b"lose",
        b"loses",
        b"lost",
        b"means",
        b"name",
        b"named",
        b"official",
        b"overcame",
        b"overcome",
        b"overcomes",
        b"paint",
        b"painted",
        b"refer",
        b"refers",
        b"runs",
        b"serve",
        b"served",
        b"serves",
        b"situated",
        b"speak",
        b"speaks",
        b"spoken",
        b"symbol",
        b"use",
        b"used",
        b"uses",
        b"win",
        b"wins",
        b"won",
        b"write",
        b"writes",
        b"wrote",
        b"written",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

const FACT_RELATION_AUTHOR: u64 = 1;
const FACT_RELATION_DEFEAT: u64 = 2;
const FACT_RELATION_TEACH: u64 = 3;

const FACT_PREDICATE_CONNECTOR_NONE: u8 = 0;
const FACT_PREDICATE_CONNECTOR_FROM: u8 = 1;
const FACT_PREDICATE_CONNECTOR_TO: u8 = 2;
const FACT_PREDICATE_CONNECTOR_AGAINST: u8 = 3;

#[derive(Clone, Copy)]
struct DirectionalFactPredicate {
    relation: u64,
    inverse: bool,
    required_connector: u8,
}

fn directional_fact_predicate(
    text: &str,
    token: crate::text::Token<'_>,
) -> Option<DirectionalFactPredicate> {
    let word = token.bytes;
    if token_eq(word, b"won") && is_negative_contraction(text, token) {
        return None;
    }

    let predicate = if [
        b"authored".as_slice(),
        b"write",
        b"writes",
        b"written",
        b"wrote",
    ]
    .iter()
    .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_AUTHOR,
            inverse: false,
            required_connector: FACT_PREDICATE_CONNECTOR_NONE,
        }
    } else if [
        b"beat".as_slice(),
        b"beaten",
        b"beats",
        b"defeat",
        b"defeated",
        b"defeats",
        b"overcame",
        b"overcome",
        b"overcomes",
    ]
    .iter()
    .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_DEFEAT,
            inverse: false,
            required_connector: FACT_PREDICATE_CONNECTOR_NONE,
        }
    } else if [b"win".as_slice(), b"wins", b"won"]
        .iter()
        .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_DEFEAT,
            inverse: false,
            required_connector: FACT_PREDICATE_CONNECTOR_AGAINST,
        }
    } else if [b"lose".as_slice(), b"loses", b"lost"]
        .iter()
        .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_DEFEAT,
            inverse: true,
            required_connector: FACT_PREDICATE_CONNECTOR_TO,
        }
    } else if [b"teach".as_slice(), b"teaches", b"taught"]
        .iter()
        .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_TEACH,
            inverse: false,
            required_connector: FACT_PREDICATE_CONNECTOR_NONE,
        }
    } else if [b"learn".as_slice(), b"learned", b"learns"]
        .iter()
        .any(|value| token_eq(word, value))
    {
        DirectionalFactPredicate {
            relation: FACT_RELATION_TEACH,
            inverse: true,
            required_connector: FACT_PREDICATE_CONNECTOR_FROM,
        }
    } else {
        const NON_DIRECTIONAL_PAST_FORMS: &[&[u8]] = &[
            b"based",
            b"called",
            b"connected",
            b"expected",
            b"located",
            b"married",
            b"named",
            b"related",
            b"shared",
            b"situated",
            b"united",
            b"used",
        ];
        let canonical = if [b"build".as_slice(), b"builds", b"built"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"build".as_slice()
        } else if [b"compose".as_slice(), b"composes", b"composed"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"compose".as_slice()
        } else if [b"create".as_slice(), b"creates", b"created"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"create".as_slice()
        } else if [b"direct".as_slice(), b"directs", b"directed"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"direct".as_slice()
        } else if [b"discover".as_slice(), b"discovers", b"discovered"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"discover".as_slice()
        } else if [b"founds".as_slice(), b"founded"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"founded".as_slice()
        } else if token_eq(word, b"found") {
            b"found".as_slice()
        } else if [b"invent".as_slice(), b"invents", b"invented"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"invent".as_slice()
        } else if [b"kill".as_slice(), b"kills", b"killed"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"kill".as_slice()
        } else if [b"lead".as_slice(), b"leads", b"led"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"lead".as_slice()
        } else if [b"paint".as_slice(), b"paints", b"painted"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"paint".as_slice()
        } else if [b"play".as_slice(), b"plays", b"played"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"play".as_slice()
        } else if [b"prepare".as_slice(), b"prepares", b"prepared"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"prepare".as_slice()
        } else if [b"produce".as_slice(), b"produces", b"produced"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"produce".as_slice()
        } else if [b"sing".as_slice(), b"sings", b"sang", b"sung"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"sing".as_slice()
        } else if [b"mentor".as_slice(), b"mentors", b"mentored"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"mentor".as_slice()
        } else if [b"admire".as_slice(), b"admires", b"admired"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"admire".as_slice()
        } else if [b"hire".as_slice(), b"hires", b"hired"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"hire".as_slice()
        } else if [b"rescue".as_slice(), b"rescues", b"rescued"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"rescue".as_slice()
        } else if [b"inspire".as_slice(), b"inspires", b"inspired"]
            .iter()
            .any(|value| token_eq(word, value))
        {
            b"inspire".as_slice()
        } else if word.len() > 4
            && word
                .get(word.len().saturating_sub(2)..)
                .is_some_and(|suffix| suffix.eq_ignore_ascii_case(b"ed"))
            && !NON_DIRECTIONAL_PAST_FORMS
                .iter()
                .any(|value| token_eq(word, value))
        {
            word
        } else {
            return None;
        };
        DirectionalFactPredicate {
            relation: crate::text::hash_lower(canonical) | (1u64 << 63),
            inverse: false,
            required_connector: FACT_PREDICATE_CONNECTOR_NONE,
        }
    };
    Some(predicate)
}

fn is_fact_negation(text: &str, token: crate::text::Token<'_>) -> bool {
    token_eq(token.bytes, b"cannot")
        || token_eq(token.bytes, b"never")
        || token_eq(token.bytes, b"no")
        || token_eq(token.bytes, b"not")
        || is_negative_contraction(text, token)
}

fn is_fact_refutation(token: &[u8]) -> bool {
    token_eq(token, b"false") || token_eq(token, b"incorrect") || token_eq(token, b"wrong")
}

fn is_fact_affirmation(token: &[u8]) -> bool {
    token_eq(token, b"correct") || token_eq(token, b"right") || token_eq(token, b"true")
}

fn is_fact_contrast(token: &[u8]) -> bool {
    token_eq(token, b"but")
        || token_eq(token, b"however")
        || token_eq(token, b"instead")
        || token_eq(token, b"rather")
        || token_eq(token, b"though")
        || token_eq(token, b"yet")
}

fn is_choice_connector(token: &[u8]) -> bool {
    token_eq(token, b"alongside")
        || token_eq(token, b"and")
        || token_eq(token, b"or")
        || token_eq(token, b"versus")
        || token_eq(token, b"vs")
}

fn is_fact_person_pronoun(token: &[u8]) -> bool {
    token_eq(token, b"he")
        || token_eq(token, b"her")
        || token_eq(token, b"him")
        || token_eq(token, b"she")
        || token_eq(token, b"them")
        || token_eq(token, b"they")
}

fn is_name_connector(token: &[u8]) -> bool {
    token_eq(token, b"and")
        || token_eq(token, b"da")
        || token_eq(token, b"de")
        || token_eq(token, b"of")
        || token_eq(token, b"the")
        || token_eq(token, b"van")
        || token_eq(token, b"von")
}

fn is_sentence_lead_article(token: &[u8]) -> bool {
    token_eq(token, b"a") || token_eq(token, b"an") || token_eq(token, b"the")
}

fn is_name_bridge(token: &[u8]) -> bool {
    token_eq(token, b"da")
        || token_eq(token, b"de")
        || token_eq(token, b"of")
        || token_eq(token, b"the")
        || token_eq(token, b"van")
        || token_eq(token, b"von")
}

fn person_name_suffix_class(token: &[u8]) -> u8 {
    if token_eq(token, b"jr") || token_eq(token, b"junior") {
        1
    } else if token_eq(token, b"sr") || token_eq(token, b"senior") {
        2
    } else {
        0
    }
}

fn is_name_suffix(token: &[u8]) -> bool {
    person_name_suffix_class(token) != 0
}

fn token_is_all_uppercase(token: &[u8]) -> bool {
    let mut letters = 0usize;
    for byte in token {
        if byte.is_ascii_alphabetic() {
            letters += 1;
            if !byte.is_ascii_uppercase() {
                return false;
            }
        }
    }
    letters > 0 && !(letters == 1 && token_eq(token, b"I"))
}

fn token_is_titlecase(token: &[u8]) -> bool {
    let Some(first_index) = token.iter().position(u8::is_ascii_alphabetic) else {
        return false;
    };
    if !token[first_index].is_ascii_uppercase() || token_eq(token, b"I") {
        return false;
    }
    token[first_index + 1..]
        .iter()
        .filter(|byte| byte.is_ascii_alphabetic())
        .all(u8::is_ascii_lowercase)
}

fn token_is_entity_like(token: &[u8]) -> bool {
    token_is_all_uppercase(token) || token_is_titlecase(token)
}

fn has_clause_boundary(text: &str, left_end: usize, right_start: usize) -> bool {
    text.as_bytes()
        .get(left_end..right_start)
        .is_some_and(|gap| {
            gap.iter()
                .any(|byte| matches!(byte, b',' | b';' | b'.' | b'!' | b'?' | b'\n'))
        })
}

fn has_strong_clause_boundary(text: &str, left_end: usize, right_start: usize) -> bool {
    text.as_bytes()
        .get(left_end..right_start)
        .is_some_and(|gap| {
            gap.iter()
                .any(|byte| matches!(byte, b';' | b'.' | b'!' | b'?' | b'\n'))
        })
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum DirectedFactBoundary {
    None,
    Comma,
    Semicolon,
    Terminal,
}

fn directed_fact_boundary(text: &str, left_end: usize, right_start: usize) -> DirectedFactBoundary {
    let Some(gap) = text.as_bytes().get(left_end..right_start) else {
        return DirectedFactBoundary::None;
    };
    if gap
        .iter()
        .any(|byte| matches!(byte, b'.' | b'!' | b'?' | b'\n'))
    {
        DirectedFactBoundary::Terminal
    } else if gap.contains(&b';') {
        DirectedFactBoundary::Semicolon
    } else if gap.contains(&b',') {
        DirectedFactBoundary::Comma
    } else {
        DirectedFactBoundary::None
    }
}

fn retain_longer_acronym(
    best: &mut [u8; 16],
    best_len: &mut usize,
    current: &mut [u8; 16],
    current_len: &mut usize,
) {
    if *current_len > *best_len {
        best[..*current_len].copy_from_slice(&current[..*current_len]);
        *best_len = *current_len;
    }
    *current = [0; 16];
    *current_len = 0;
}

fn fact_anchor_acronym(ground_truth: &str) -> ([u8; 16], usize) {
    let mut best = [0u8; 16];
    let mut best_len = 0usize;
    let mut current = [0u8; 16];
    let mut current_len = 0usize;
    let mut previous_end = 0usize;

    for token in TokenIter::new(ground_truth) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(ground_truth, previous_end, start) {
            retain_longer_acronym(&mut best, &mut best_len, &mut current, &mut current_len);
        }
        previous_end = token.end;
        if is_name_suffix(token.bytes) && current_len > 0 {
            continue;
        }
        if token_is_entity_like(token.bytes)
            && semantic_hash(token.bytes).is_some()
            && !is_name_connector(token.bytes)
        {
            if current_len < current.len()
                && let Some(first) = token.bytes.iter().find(|byte| byte.is_ascii_alphabetic())
            {
                current[current_len] = first.to_ascii_lowercase();
                current_len += 1;
            }
            continue;
        }
        if current_len > 0 && is_name_connector(token.bytes) {
            continue;
        }
        retain_longer_acronym(&mut best, &mut best_len, &mut current, &mut current_len);
    }
    retain_longer_acronym(&mut best, &mut best_len, &mut current, &mut current_len);
    if best_len >= 2 {
        (best, best_len)
    } else {
        ([0; 16], 0)
    }
}

fn token_matches_fact_acronym(token: &[u8], acronym: &[u8; 16], acronym_len: usize) -> bool {
    acronym_len >= 2
        && token.len() == acronym_len
        && token_is_all_uppercase(token)
        && token
            .iter()
            .zip(acronym[..acronym_len].iter())
            .all(|(left, right)| left.to_ascii_lowercase() == *right)
}

fn text_contains_fact_acronym(text: &str, acronym: &[u8; 16], acronym_len: usize) -> bool {
    TokenIter::new(text).any(|token| token_matches_fact_acronym(token.bytes, acronym, acronym_len))
}

struct FactAnchors {
    values: HashSet<MAX_FACT_ANCHORS>,
    context_constraints: HashSet<MAX_FACT_ANCHORS>,
    preferred_entities: bool,
    primary_value: Option<u64>,
    acronym: [u8; 16],
    acronym_len: usize,
}

fn is_weather_anchor_signal(token: &[u8]) -> bool {
    core::str::from_utf8(token).is_ok_and(|value| weather_concept_mask(value) != 0)
}

fn is_temporal_or_unit_anchor(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"c",
        b"celsius",
        b"current",
        b"currently",
        b"day",
        b"days",
        b"degree",
        b"degrees",
        b"f",
        b"fahrenheit",
        b"hour",
        b"hours",
        b"later",
        b"mm",
        b"month",
        b"months",
        b"now",
        b"percent",
        b"percentage",
        b"today",
        b"tomorrow",
        b"tonight",
        b"utc",
        b"week",
        b"weeks",
        b"year",
        b"years",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

fn is_context_provenance_or_modal(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"according",
        b"call",
        b"calls",
        b"could",
        b"data",
        b"ensemble",
        b"ensembles",
        b"expect",
        b"expected",
        b"expects",
        b"forecast",
        b"forecasted",
        b"forecasts",
        b"guidance",
        b"heavy",
        b"indicate",
        b"indicated",
        b"indicates",
        b"intermittent",
        b"isolated",
        b"light",
        b"likely",
        b"may",
        b"might",
        b"model",
        b"models",
        b"moderate",
        b"occasional",
        b"patchy",
        b"per",
        b"possible",
        b"predict",
        b"predicted",
        b"predicts",
        b"report",
        b"reported",
        b"reports",
        b"scattered",
        b"said",
        b"say",
        b"says",
        b"should",
        b"source",
        b"sources",
        b"steady",
        b"suggest",
        b"suggested",
        b"suggests",
        b"widespread",
        b"would",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

fn is_source_attribution_cue(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"according",
        b"data",
        b"ensemble",
        b"ensembles",
        b"expect",
        b"expected",
        b"expects",
        b"forecast",
        b"forecasted",
        b"forecasts",
        b"guidance",
        b"indicate",
        b"indicated",
        b"indicates",
        b"model",
        b"models",
        b"predict",
        b"predicted",
        b"predicts",
        b"report",
        b"reported",
        b"reports",
        b"said",
        b"say",
        b"says",
        b"source",
        b"sources",
        b"suggest",
        b"suggested",
        b"suggests",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

fn context_candidate_hash(
    question_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    truth_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    token: crate::text::Token<'_>,
) -> Option<u64> {
    let hash = semantic_hash(token.bytes)?;
    if question_tokens.contains(hash)
        || truth_tokens.contains(hash)
        || is_fact_relation_or_filler(token.bytes)
        || is_context_provenance_or_modal(token.bytes)
        || is_weather_anchor_signal(token.bytes)
        || is_temporal_or_unit_anchor(token.bytes)
        || token_is_all_uppercase(token.bytes)
        || token.bytes.iter().any(u8::is_ascii_digit)
    {
        None
    } else {
        Some(hash)
    }
}

fn context_source_name_hashes(
    question_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    truth_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    answer: &str,
) -> HashSet<MAX_FACT_ANCHORS> {
    let mut sources = HashSet::new();
    let mut pending = HashSet::<MAX_FACT_ANCHORS>::new();
    let mut pending_age = u8::MAX;
    let mut according_scope = 0u8;
    let mut previous_end = 0usize;

    for token in TokenIter::new(answer) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_strong_clause_boundary(answer, previous_end, start) {
            pending = HashSet::new();
            pending_age = u8::MAX;
            according_scope = 0;
        }
        previous_end = token.end;

        if token_eq(token.bytes, b"according") {
            pending = HashSet::new();
            pending_age = u8::MAX;
            according_scope = 4;
            continue;
        }
        if is_source_attribution_cue(token.bytes) {
            for hash in &pending.values[..pending.len] {
                sources.insert(*hash);
            }
            pending = HashSet::new();
            pending_age = u8::MAX;
            according_scope = 0;
            continue;
        }

        if let Some(hash) = context_candidate_hash(question_tokens, truth_tokens, token) {
            if according_scope > 0 {
                sources.insert(hash);
            } else if pending_age <= 2 || pending.len == 0 {
                pending.insert(hash);
                pending_age = 0;
            }
        } else if semantic_hash(token.bytes).is_some() && !is_name_connector(token.bytes) {
            pending_age = pending_age.saturating_add(1);
            if pending_age > 2 {
                pending = HashSet::new();
                pending_age = u8::MAX;
            }
        }
        according_scope = according_scope.saturating_sub(1);
    }
    sources
}

fn novel_context_candidate_count(
    question_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    truth_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    answer: &str,
) -> usize {
    let mut candidates = HashSet::<MAX_FACT_ANCHORS>::new();
    let source_names = context_source_name_hashes(question_tokens, truth_tokens, answer);
    for token in TokenIter::new(answer) {
        if let Some(hash) = context_candidate_hash(question_tokens, truth_tokens, token)
            && !source_names.contains(hash)
        {
            candidates.insert(hash);
        }
    }
    candidates.len
}

fn fact_anchors(question: &str, ground_truth: &str, weather_question: bool) -> FactAnchors {
    let question_tokens = semantic_set(question);
    let truth_tokens = semantic_set(ground_truth);
    let mut preferred = HashSet::new();
    let mut fallback = HashSet::new();
    let mut context_constraints = HashSet::new();
    let mut primary_value = None;
    let mut first_entity_span_started = false;
    let mut first_entity_span_open = false;
    let mut at_clause_start = true;
    let mut previous_end = 0usize;
    for token in TokenIter::new(ground_truth) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(ground_truth, previous_end, start) {
            first_entity_span_open = false;
            at_clause_start = true;
        }
        previous_end = token.end;
        if at_clause_start && is_sentence_lead_article(token.bytes) {
            continue;
        }
        let excluded_domain_signal = weather_question
            && (is_weather_anchor_signal(token.bytes)
                || is_temporal_or_unit_anchor(token.bytes)
                || token.bytes.iter().any(u8::is_ascii_digit));
        let relation_token = is_fact_relation_or_filler(token.bytes) || excluded_domain_signal;
        if relation_token {
            at_clause_start = false;
        }
        if relation_token || token.bytes.iter().any(u8::is_ascii_digit) {
            if first_entity_span_open {
                first_entity_span_open = false;
            }
            continue;
        }
        if first_entity_span_open
            && !token_is_entity_like(token.bytes)
            && !is_name_bridge(token.bytes)
        {
            first_entity_span_open = false;
        }
        if first_entity_span_open && is_name_suffix(token.bytes) {
            at_clause_start = false;
            continue;
        }
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        if question_tokens.contains(hash) {
            at_clause_start = false;
            continue;
        }
        fallback.insert(hash);
        if token_is_entity_like(token.bytes) {
            preferred.insert(hash);
            if !first_entity_span_started {
                first_entity_span_started = true;
                first_entity_span_open = true;
                primary_value = Some(hash);
            } else if first_entity_span_open && !is_name_suffix(token.bytes) {
                primary_value = Some(hash);
            }
        }
        at_clause_start = false;
    }
    let preferred_entities = preferred.len > 0;
    let values = if preferred_entities {
        preferred
    } else {
        fallback
    };
    let (acronym, acronym_len) = fact_anchor_acronym(ground_truth);
    if weather_question {
        for token in TokenIter::new(question) {
            let Some(hash) = semantic_hash(token.bytes) else {
                continue;
            };
            if !truth_tokens.contains(hash)
                || is_fact_relation_or_filler(token.bytes)
                || is_weather_anchor_signal(token.bytes)
                || is_temporal_or_unit_anchor(token.bytes)
                || token.bytes.iter().any(u8::is_ascii_digit)
            {
                continue;
            }
            context_constraints.insert(hash);
        }
    }
    FactAnchors {
        values,
        context_constraints,
        preferred_entities,
        primary_value,
        acronym,
        acronym_len,
    }
}

fn fact_anchor_entity_representatives(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
) -> HashSet<MAX_FACT_ANCHORS> {
    let mut representatives = HashSet::new();
    let mut pending = None;
    let mut previous_end = 0usize;

    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start)
            && let Some(hash) = pending.take()
        {
            representatives.insert(hash);
        }
        previous_end = token.end;

        if let Some(hash) = semantic_hash(token.bytes)
            && anchors.contains(hash)
        {
            pending = Some(hash);
            continue;
        }
        if pending.is_some() && is_name_bridge(token.bytes) {
            continue;
        }
        if let Some(hash) = pending.take() {
            representatives.insert(hash);
        }
    }
    if let Some(hash) = pending {
        representatives.insert(hash);
    }
    representatives
}

fn person_name_suffix_mask(text: &str, anchors: &HashSet<MAX_FACT_ANCHORS>) -> u8 {
    let mut mask = 0u8;
    let mut anchor_span_open = false;
    let mut previous_end = 0usize;

    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start) {
            anchor_span_open = false;
        }
        previous_end = token.end;

        let suffix = person_name_suffix_class(token.bytes);
        if anchor_span_open && suffix != 0 {
            mask |= 1 << (suffix - 1);
            anchor_span_open = false;
            continue;
        }
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        if anchors.contains(hash) && token_is_entity_like(token.bytes) {
            anchor_span_open = true;
        } else if anchor_span_open && !is_name_bridge(token.bytes) {
            anchor_span_open = false;
        }
    }
    mask
}

fn fact_pair_hash(left: u64, right: u64) -> u64 {
    let (first, second) = if left <= right {
        (left, right)
    } else {
        (right, left)
    };
    first.rotate_left(17) ^ second.wrapping_mul(0x9e3779b185ebca87)
}

fn directed_fact_relation_hash(relation: u64, actor: u64, target: u64) -> u64 {
    actor.wrapping_mul(0x9e3779b185ebca87)
        ^ target.rotate_left(29)
        ^ relation.rotate_left(41).wrapping_mul(0xa24baed4963ee407)
        ^ 0xd6e8feb86659fd93
}

#[cfg(test)]
fn directed_fact_pair_hash(actor: u64, target: u64) -> u64 {
    directed_fact_relation_hash(FACT_RELATION_DEFEAT, actor, target)
}

fn directed_fact_entity_role(kind: u8) -> u8 {
    match kind {
        1 | 3 => 1,
        2 => 2,
        _ => 0,
    }
}

fn directed_fact_entity_kinds_match(left: u8, right: u8) -> bool {
    let left_role = directed_fact_entity_role(left);
    let right_role = directed_fact_entity_role(right);
    left_role != 0 && right_role != 0 && left_role != right_role
}

fn fact_predicate_connector_matches(required: u8, token: &[u8]) -> bool {
    (required == FACT_PREDICATE_CONNECTOR_FROM && token_eq(token, b"from"))
        || (required == FACT_PREDICATE_CONNECTOR_TO && token_eq(token, b"to"))
        || (required == FACT_PREDICATE_CONNECTOR_AGAINST && token_eq(token, b"against"))
}

fn has_malformed_connector_directed_claim(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
    question_entities: &HashSet<MAX_FACT_ANCHORS>,
) -> bool {
    let mut pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
    let mut previous_end = 0usize;
    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start) || is_fact_contrast(token.bytes) {
            if pending_connector != FACT_PREDICATE_CONNECTOR_NONE {
                return true;
            }
            pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
        }
        previous_end = token.end;
        if pending_connector != FACT_PREDICATE_CONNECTOR_NONE
            && fact_predicate_connector_matches(pending_connector, token.bytes)
        {
            pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
            continue;
        }
        if let Some(predicate) = directional_fact_predicate(text, token) {
            if pending_connector != FACT_PREDICATE_CONNECTOR_NONE {
                return true;
            }
            pending_connector = predicate.required_connector;
            continue;
        }
        if pending_connector != FACT_PREDICATE_CONNECTOR_NONE
            && semantic_hash(token.bytes).is_some_and(|hash| {
                anchors.contains(hash)
                    || question_entities.contains(hash)
                    || token_is_entity_like(token.bytes)
            })
        {
            return true;
        }
    }
    pending_connector != FACT_PREDICATE_CONNECTOR_NONE
}

fn has_multiple_directed_fact_syntax(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
    question_entities: &HashSet<MAX_FACT_ANCHORS>,
) -> bool {
    let mut predicate_count = 0usize;
    let mut pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
    let mut last_entity_role = 0u8;
    let mut conjunction_left_role = 0u8;
    let mut previous_end = 0usize;
    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start) || is_fact_contrast(token.bytes) {
            pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
        }
        previous_end = token.end;
        if token_eq(token.bytes, b"and") {
            conjunction_left_role = last_entity_role;
            continue;
        }
        if pending_connector != FACT_PREDICATE_CONNECTOR_NONE
            && fact_predicate_connector_matches(pending_connector, token.bytes)
        {
            predicate_count += 1;
            pending_connector = FACT_PREDICATE_CONNECTOR_NONE;
            continue;
        }
        if let Some(predicate) = directional_fact_predicate(text, token) {
            if predicate.required_connector == FACT_PREDICATE_CONNECTOR_NONE {
                predicate_count += 1;
            } else {
                pending_connector = predicate.required_connector;
            }
        }
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        let role = if anchors.contains(hash) {
            1
        } else if question_entities.contains(hash) {
            2
        } else {
            0
        };
        if role == 0 {
            continue;
        }
        if conjunction_left_role == role {
            return true;
        }
        last_entity_role = role;
        conjunction_left_role = 0;
    }
    predicate_count > 1
}

fn is_interrogative(token: &[u8]) -> bool {
    token_eq(token, b"how")
        || token_eq(token, b"what")
        || token_eq(token, b"when")
        || token_eq(token, b"where")
        || token_eq(token, b"which")
        || token_eq(token, b"who")
        || token_eq(token, b"whom")
        || token_eq(token, b"whose")
}

fn question_entity_set(question: &str, ground_truth: &str) -> HashSet<MAX_FACT_ANCHORS> {
    let mut truth_entities = HashSet::<MAX_FACT_ANCHORS>::new();
    for token in TokenIter::new(ground_truth) {
        if token_is_entity_like(token.bytes)
            && let Some(hash) = semantic_hash(token.bytes)
        {
            truth_entities.insert(hash);
        }
    }

    let mut entities = HashSet::new();
    for token in TokenIter::new(question) {
        if is_interrogative(token.bytes)
            || is_fact_relation_or_filler(token.bytes)
            || is_name_suffix(token.bytes)
        {
            continue;
        }
        if let Some(hash) = semantic_hash(token.bytes)
            && (token_is_entity_like(token.bytes) || truth_entities.contains(hash))
        {
            entities.insert(hash);
        }
    }
    entities
}

fn fact_directed_relation_pairs(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
    question_entities: &HashSet<MAX_FACT_ANCHORS>,
) -> HashSet<MAX_FACT_RELATION_PAIRS> {
    let mut pairs = HashSet::new();
    let mut before_hashes = [0u64; FACT_RELATION_WINDOW];
    let mut before_kinds = [0u8; FACT_RELATION_WINDOW];
    let mut before_len = 0usize;
    let mut predicate_seen = false;
    let mut predicate_relation = 0u64;
    let mut predicate_inverse = false;
    let mut predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
    let mut predicate_connector_seen = true;
    let mut passive_by_seen = false;
    let mut relation_argument_seen = false;
    let mut relation_argument_conjunction = false;
    let mut active_actor_role = 0u8;
    let mut gapped_actor_role = 0u8;
    let mut gapped_relation = 0u64;
    let mut gapped_inverse = false;
    let mut gapped_separator_seen = false;
    let mut completed_gapped_actor_role = 0u8;
    let mut completed_gapped_relation = 0u64;
    let mut completed_gapped_inverse = false;
    let mut completed_gapped_subject = None;
    let mut gapped_argument_continuation = false;
    let mut before_entity_conjunction = false;
    let mut elliptical_passive_targets = [0u64; FACT_RELATION_WINDOW];
    let mut elliptical_passive_target_len = 0usize;
    let mut elliptical_passive_relation = 0u64;
    let mut elliptical_actor_seen = false;
    let mut completed_relation_in_clause = 0u64;
    let mut pending_bare_by_relation = 0u64;
    let mut pending_bare_by_new_segment = true;
    let mut passive_ellipsis_actor = None;
    let mut passive_ellipsis_scope = 0u8;
    let mut passive_ellipsis_so_seen = false;
    let mut previous_end = 0usize;

    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        let boundary = directed_fact_boundary(text, previous_end, start);
        let clause_boundary = boundary != DirectedFactBoundary::None;
        let strong_clause_boundary = matches!(
            boundary,
            DirectedFactBoundary::Semicolon | DirectedFactBoundary::Terminal
        );
        if strong_clause_boundary {
            pending_bare_by_relation = if boundary == DirectedFactBoundary::Semicolon {
                completed_relation_in_clause
            } else {
                0
            };
            completed_relation_in_clause = 0;
            if boundary == DirectedFactBoundary::Semicolon
                && predicate_seen
                && relation_argument_seen
                && active_actor_role != 0
            {
                gapped_actor_role = active_actor_role;
                gapped_relation = predicate_relation;
                gapped_inverse = predicate_inverse;
            } else if boundary == DirectedFactBoundary::Semicolon && completed_gapped_relation != 0
            {
                gapped_actor_role = completed_gapped_actor_role;
                gapped_relation = completed_gapped_relation;
                gapped_inverse = completed_gapped_inverse;
            } else {
                gapped_actor_role = 0;
                gapped_relation = 0;
                gapped_inverse = false;
            }
            before_len = 0;
            predicate_seen = false;
            predicate_relation = 0;
            predicate_inverse = false;
            predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
            predicate_connector_seen = true;
            passive_by_seen = false;
            relation_argument_seen = false;
            relation_argument_conjunction = false;
            active_actor_role = 0;
            gapped_separator_seen = false;
            completed_gapped_actor_role = 0;
            completed_gapped_relation = 0;
            completed_gapped_inverse = false;
            completed_gapped_subject = None;
            gapped_argument_continuation = false;
            before_entity_conjunction = false;
            elliptical_passive_target_len = 0;
            elliptical_passive_relation = 0;
            elliptical_actor_seen = false;
            pending_bare_by_new_segment = true;
            passive_ellipsis_actor = None;
            passive_ellipsis_scope = 0;
            passive_ellipsis_so_seen = false;
        } else if (clause_boundary && elliptical_actor_seen) || is_fact_contrast(token.bytes) {
            before_len = 0;
            predicate_seen = false;
            predicate_relation = 0;
            predicate_inverse = false;
            predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
            predicate_connector_seen = true;
            passive_by_seen = false;
            relation_argument_seen = false;
            relation_argument_conjunction = false;
            active_actor_role = 0;
            gapped_actor_role = 0;
            gapped_relation = 0;
            gapped_inverse = false;
            gapped_separator_seen = false;
            completed_gapped_actor_role = 0;
            completed_gapped_relation = 0;
            completed_gapped_inverse = false;
            completed_gapped_subject = None;
            gapped_argument_continuation = false;
            before_entity_conjunction = false;
            elliptical_passive_target_len = 0;
            elliptical_passive_relation = 0;
            elliptical_actor_seen = false;
            completed_relation_in_clause = 0;
            pending_bare_by_relation = 0;
            pending_bare_by_new_segment = true;
            passive_ellipsis_actor = None;
            passive_ellipsis_scope = 0;
            passive_ellipsis_so_seen = false;
        } else if clause_boundary && gapped_actor_role != 0 && before_len > 0 && !predicate_seen {
            gapped_separator_seen = true;
        }
        previous_end = token.end;
        if gapped_actor_role != 0 && before_len == 0 && !clause_boundary {
            gapped_actor_role = 0;
            gapped_relation = 0;
            gapped_inverse = false;
        }
        if is_fact_person_pronoun(token.bytes) {
            before_len = 0;
            predicate_seen = false;
            predicate_relation = 0;
            predicate_inverse = false;
            predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
            predicate_connector_seen = true;
            passive_by_seen = false;
            relation_argument_seen = false;
            relation_argument_conjunction = false;
            active_actor_role = 0;
            gapped_actor_role = 0;
            gapped_relation = 0;
            gapped_inverse = false;
            gapped_separator_seen = false;
            before_entity_conjunction = false;
            elliptical_passive_target_len = 0;
            elliptical_passive_relation = 0;
            elliptical_actor_seen = false;
            completed_gapped_actor_role = 0;
            completed_gapped_relation = 0;
            completed_gapped_inverse = false;
            completed_gapped_subject = None;
            gapped_argument_continuation = false;
            completed_relation_in_clause = 0;
            pending_bare_by_relation = 0;
            pending_bare_by_new_segment = true;
            passive_ellipsis_actor = None;
            passive_ellipsis_scope = 0;
            passive_ellipsis_so_seen = false;
            continue;
        }
        if pending_bare_by_relation != 0 && token_eq(token.bytes, b"and") {
            pending_bare_by_new_segment = true;
        }
        if pending_bare_by_relation != 0
            && !token_eq(token.bytes, b"by")
            && !is_name_suffix(token.bytes)
            && !is_name_connector(token.bytes)
            && !is_sentence_lead_article(token.bytes)
            && !semantic_hash(token.bytes).is_some_and(|hash| question_entities.contains(hash))
        {
            pending_bare_by_relation = 0;
            elliptical_passive_target_len = 0;
            pending_bare_by_new_segment = true;
        }
        if passive_ellipsis_scope > 0 && token_eq(token.bytes, b"so") {
            passive_ellipsis_so_seen = true;
            continue;
        }
        if token_eq(token.bytes, b"and") {
            if clause_boundary && passive_ellipsis_actor.is_some() {
                before_len = 0;
                predicate_seen = false;
                predicate_relation = 0;
                predicate_inverse = false;
                predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
                predicate_connector_seen = true;
                passive_by_seen = false;
                relation_argument_seen = false;
                relation_argument_conjunction = false;
                active_actor_role = 0;
                gapped_actor_role = 0;
                gapped_relation = 0;
                gapped_inverse = false;
                gapped_separator_seen = false;
                before_entity_conjunction = false;
                elliptical_passive_target_len = 0;
                elliptical_passive_relation = 0;
                elliptical_actor_seen = false;
                passive_ellipsis_scope = 4;
                passive_ellipsis_so_seen = false;
            } else if gapped_argument_continuation {
                if let Some((hash, kind)) = completed_gapped_subject {
                    before_hashes[0] = hash;
                    before_kinds[0] = kind;
                    before_len = 1;
                    gapped_actor_role = completed_gapped_actor_role;
                    gapped_relation = completed_gapped_relation;
                    gapped_inverse = completed_gapped_inverse;
                    gapped_separator_seen = true;
                }
                gapped_argument_continuation = false;
            } else if elliptical_actor_seen {
                before_len = 0;
                predicate_seen = false;
                predicate_relation = 0;
                predicate_inverse = false;
                predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
                predicate_connector_seen = true;
                passive_by_seen = false;
                relation_argument_seen = false;
                relation_argument_conjunction = false;
                active_actor_role = 0;
                gapped_actor_role = 0;
                gapped_relation = 0;
                gapped_inverse = false;
                gapped_separator_seen = false;
                before_entity_conjunction = false;
                elliptical_passive_target_len = 0;
                elliptical_passive_relation = 0;
                elliptical_actor_seen = false;
            } else if predicate_seen && relation_argument_seen {
                relation_argument_conjunction = true;
            } else {
                before_entity_conjunction = true;
            }
            continue;
        }
        if let Some(predicate) = directional_fact_predicate(text, token) {
            pending_bare_by_relation = 0;
            pending_bare_by_new_segment = true;
            predicate_seen = before_len > 0;
            predicate_relation = predicate.relation;
            predicate_inverse = predicate.inverse;
            predicate_required_connector = predicate.required_connector;
            predicate_connector_seen =
                predicate.required_connector == FACT_PREDICATE_CONNECTOR_NONE;
            passive_by_seen = false;
            relation_argument_seen = false;
            relation_argument_conjunction = false;
            active_actor_role = 0;
            gapped_actor_role = 0;
            gapped_relation = 0;
            gapped_inverse = false;
            gapped_separator_seen = false;
            completed_gapped_actor_role = 0;
            completed_gapped_relation = 0;
            completed_gapped_inverse = false;
            completed_gapped_subject = None;
            gapped_argument_continuation = false;
            before_entity_conjunction = false;
            elliptical_passive_target_len = 0;
            elliptical_passive_relation = 0;
            elliptical_actor_seen = false;
            passive_ellipsis_actor = None;
            passive_ellipsis_scope = 0;
            passive_ellipsis_so_seen = false;
            continue;
        }
        if predicate_seen
            && fact_predicate_connector_matches(predicate_required_connector, token.bytes)
        {
            predicate_connector_seen = true;
            continue;
        }
        if token_eq(token.bytes, b"by") {
            if predicate_seen && !relation_argument_seen {
                passive_by_seen = true;
            } else if !predicate_seen {
                elliptical_passive_relation = if elliptical_passive_target_len > 0 {
                    pending_bare_by_relation
                } else {
                    0
                };
                pending_bare_by_relation = 0;
                pending_bare_by_new_segment = true;
                elliptical_actor_seen = false;
                if elliptical_passive_relation != 0 {
                    before_len = 0;
                } else {
                    elliptical_passive_target_len = 0;
                }
            }
            continue;
        }
        if is_name_suffix(token.bytes) {
            continue;
        }
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        // Novel named entities remain answer-side so extra directed claims cannot disappear.
        let kind = if anchors.contains(hash) {
            1u8
        } else if question_entities.contains(hash) {
            2u8
        } else if token_is_entity_like(token.bytes)
            && !is_fact_relation_or_filler(token.bytes)
            && !is_context_provenance_or_modal(token.bytes)
        {
            3u8
        } else {
            0u8
        };
        if kind == 0 {
            continue;
        }
        if pending_bare_by_relation != 0 && kind == 2 && pending_bare_by_new_segment {
            if elliptical_passive_target_len < FACT_RELATION_WINDOW {
                elliptical_passive_targets[elliptical_passive_target_len] = hash;
                elliptical_passive_target_len += 1;
            }
            pending_bare_by_new_segment = false;
        }
        if passive_ellipsis_scope > 0
            && passive_ellipsis_so_seen
            && let Some((actor, actor_kind, relation)) = passive_ellipsis_actor
            && directed_fact_entity_kinds_match(kind, actor_kind)
        {
            pairs.insert(directed_fact_relation_hash(relation, actor, hash));
            completed_relation_in_clause = relation;
            passive_ellipsis_scope = 0;
            passive_ellipsis_so_seen = false;
            continue;
        }
        passive_ellipsis_scope = passive_ellipsis_scope.saturating_sub(1);
        if elliptical_passive_relation != 0 {
            if directed_fact_entity_role(kind) == 1 {
                for target in &elliptical_passive_targets[..elliptical_passive_target_len] {
                    pairs.insert(directed_fact_relation_hash(
                        elliptical_passive_relation,
                        hash,
                        *target,
                    ));
                }
                elliptical_actor_seen = true;
                completed_relation_in_clause = elliptical_passive_relation;
                passive_ellipsis_actor = Some((hash, kind, elliptical_passive_relation));
            }
            continue;
        }
        if predicate_seen {
            let mut matched = false;
            if predicate_connector_seen
                && (!relation_argument_seen || relation_argument_conjunction)
            {
                for index in 0..before_len {
                    if !directed_fact_entity_kinds_match(kind, before_kinds[index]) {
                        continue;
                    }
                    let previous = before_hashes[index];
                    let (mut actor, mut target, mut actor_kind, mut target_kind) =
                        if passive_by_seen {
                            (hash, previous, kind, before_kinds[index])
                        } else {
                            (previous, hash, before_kinds[index], kind)
                        };
                    if predicate_inverse {
                        core::mem::swap(&mut actor, &mut target);
                        core::mem::swap(&mut actor_kind, &mut target_kind);
                    }
                    pairs.insert(directed_fact_relation_hash(
                        predicate_relation,
                        actor,
                        target,
                    ));
                    completed_relation_in_clause = predicate_relation;
                    if passive_by_seen {
                        passive_ellipsis_actor = Some((actor, actor_kind, predicate_relation));
                    } else {
                        active_actor_role = directed_fact_entity_role(before_kinds[index]);
                    }
                    matched = true;
                }
            }

            if matched {
                relation_argument_seen = true;
                relation_argument_conjunction = false;
                if !passive_by_seen {
                    gapped_actor_role = 0;
                    gapped_relation = 0;
                    gapped_inverse = false;
                }
                continue;
            }
            if relation_argument_seen
                && (0..before_len).any(|index| {
                    directed_fact_entity_role(before_kinds[index])
                        == directed_fact_entity_role(kind)
                })
            {
                let coordinated_gapping =
                    relation_argument_conjunction && !passive_by_seen && predicate_relation != 0;
                if coordinated_gapping {
                    gapped_actor_role = directed_fact_entity_role(kind);
                    gapped_relation = predicate_relation;
                    gapped_inverse = predicate_inverse;
                }
                before_hashes[0] = hash;
                before_kinds[0] = kind;
                before_len = 1;
                predicate_seen = false;
                passive_by_seen = false;
                relation_argument_seen = false;
                relation_argument_conjunction = false;
                active_actor_role = 0;
                predicate_relation = 0;
                predicate_inverse = false;
                predicate_required_connector = FACT_PREDICATE_CONNECTOR_NONE;
                predicate_connector_seen = true;
            }
            continue;
        }
        if gapped_separator_seen
            && gapped_relation != 0
            && directed_fact_entity_role(kind) != gapped_actor_role
            && directed_fact_entity_role(kind) != 0
        {
            let mut matched = false;
            for index in 0..before_len {
                if directed_fact_entity_role(before_kinds[index]) != gapped_actor_role {
                    continue;
                }
                let mut actor = before_hashes[index];
                let mut target = hash;
                if gapped_inverse {
                    core::mem::swap(&mut actor, &mut target);
                }
                pairs.insert(directed_fact_relation_hash(gapped_relation, actor, target));
                completed_relation_in_clause = gapped_relation;
                completed_gapped_actor_role = gapped_actor_role;
                completed_gapped_relation = gapped_relation;
                completed_gapped_inverse = gapped_inverse;
                completed_gapped_subject = Some((before_hashes[index], before_kinds[index]));
                matched = true;
            }
            if matched {
                before_len = 0;
                gapped_actor_role = 0;
                gapped_relation = 0;
                gapped_inverse = false;
                gapped_separator_seen = false;
                gapped_argument_continuation = true;
                continue;
            }
        }
        gapped_argument_continuation = false;
        if clause_boundary && kind == 1 && (0..before_len).any(|index| before_kinds[index] == 3) {
            before_len = 0;
        }
        if kind == 1
            && !before_entity_conjunction
            && (0..before_len).any(|index| before_kinds[index] == 3)
        {
            before_len = 0;
        }
        if before_len < FACT_RELATION_WINDOW {
            before_hashes[before_len] = hash;
            before_kinds[before_len] = kind;
            before_len += 1;
        } else {
            for index in 1..FACT_RELATION_WINDOW {
                before_hashes[index - 1] = before_hashes[index];
                before_kinds[index - 1] = before_kinds[index];
            }
            before_hashes[FACT_RELATION_WINDOW - 1] = hash;
            before_kinds[FACT_RELATION_WINDOW - 1] = kind;
        }
        before_entity_conjunction = false;
    }
    pairs
}

fn fact_relation_pairs(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
    question_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
) -> HashSet<MAX_FACT_RELATION_PAIRS> {
    let mut pairs = HashSet::new();
    let mut recent_hashes = [0u64; FACT_RELATION_WINDOW];
    let mut recent_kinds = [0u8; FACT_RELATION_WINDOW];
    let mut recent_len = 0usize;
    let mut previous_end = 0usize;

    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start) || is_fact_contrast(token.bytes) {
            recent_len = 0;
        }
        previous_end = token.end;
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        let kind = if anchors.contains(hash) {
            1u8
        } else if question_tokens.contains(hash) {
            2u8
        } else {
            0u8
        };
        if kind != 0 {
            for index in 0..recent_len {
                if (kind == 1 && recent_kinds[index] == 2)
                    || (kind == 2 && recent_kinds[index] == 1)
                {
                    pairs.insert(fact_pair_hash(hash, recent_hashes[index]));
                }
            }
        }
        if recent_len < FACT_RELATION_WINDOW {
            recent_hashes[recent_len] = hash;
            recent_kinds[recent_len] = kind;
            recent_len += 1;
        } else {
            for index in 1..FACT_RELATION_WINDOW {
                recent_hashes[index - 1] = recent_hashes[index];
                recent_kinds[index - 1] = recent_kinds[index];
            }
            recent_hashes[FACT_RELATION_WINDOW - 1] = hash;
            recent_kinds[FACT_RELATION_WINDOW - 1] = kind;
        }
    }
    pairs
}

fn fact_entity_pairs(
    text: &str,
    anchors: &HashSet<MAX_FACT_ANCHORS>,
) -> HashSet<MAX_FACT_RELATION_PAIRS> {
    let mut pairs = HashSet::new();
    let mut previous_anchor = None;
    let mut previous_end = 0usize;
    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(text, previous_end, start) || token_eq(token.bytes, b"and") {
            previous_anchor = None;
        }
        previous_end = token.end;
        let Some(hash) = semantic_hash(token.bytes) else {
            if !is_name_connector(token.bytes) {
                previous_anchor = None;
            }
            continue;
        };
        if anchors.contains(hash) {
            if let Some(previous) = previous_anchor
                && previous != hash
            {
                pairs.insert(fact_pair_hash(previous, hash));
            }
            previous_anchor = Some(hash);
        } else if !is_name_connector(token.bytes) {
            previous_anchor = None;
        }
    }
    pairs
}

#[derive(Clone, Copy)]
struct FactAnchorAssessment {
    support: f32,
    contradicted: bool,
    ambiguous_or_stuffed: bool,
}

fn fact_anchor_assessment(
    question: &str,
    ground_truth: &str,
    answer: &str,
    weather_question: bool,
) -> Option<FactAnchorAssessment> {
    let anchors = fact_anchors(question, ground_truth, weather_question);
    if anchors.values.len == 0 && anchors.context_constraints.len == 0 && anchors.acronym_len < 2 {
        return None;
    }
    let question_tokens = semantic_set(question);
    let question_entities = question_entity_set(question, ground_truth);
    let truth_tokens = semantic_set(ground_truth);
    let response = semantic_set(answer);
    let overlap = anchors.values.values[..anchors.values.len]
        .iter()
        .filter(|value| response.contains(**value))
        .count();
    let required_answer_entities =
        fact_anchor_entity_representatives(ground_truth, &anchors.values);
    let required_answer_entity_overlap = required_answer_entities.values
        [..required_answer_entities.len]
        .iter()
        .filter(|value| response.contains(**value))
        .count();
    let acronym_explicit_in_truth =
        text_contains_fact_acronym(ground_truth, &anchors.acronym, anchors.acronym_len);
    let acronym_candidate = anchors.acronym_len >= 2
        && !text_contains_fact_acronym(question, &anchors.acronym, anchors.acronym_len);
    let acronym_authoritative = acronym_explicit_in_truth || question_requests_acronym(question);
    let acronym_present = acronym_candidate
        && text_contains_fact_acronym(answer, &anchors.acronym, anchors.acronym_len);
    let primary_present = anchors
        .primary_value
        .is_some_and(|value| response.contains(value));
    let value_support = if primary_present {
        1.0
    } else if anchors.preferred_entities || anchors.values.len == 0 {
        0.0
    } else {
        overlap as f32 / anchors.values.len as f32
    };
    let acronym_support = if acronym_present {
        if acronym_authoritative { 1.0 } else { 0.5 }
    } else {
        0.0
    };
    let value_signal_present = primary_present || acronym_present || overlap > 0;
    let context_overlap = anchors.context_constraints.values[..anchors.context_constraints.len]
        .iter()
        .filter(|value| response.contains(**value))
        .count();
    let truth_suffix_mask = person_name_suffix_mask(ground_truth, &anchors.values);
    let answer_suffix_mask = person_name_suffix_mask(answer, &anchors.values);
    let suffix_conflict = truth_suffix_mask != 0
        && answer_suffix_mask != 0
        && truth_suffix_mask != answer_suffix_mask;

    let mut contradicted = suffix_conflict;
    let mut connector_ambiguity = false;
    let mut novel_entity_after_contrast = false;
    let mut anchor_counts = [0u8; MAX_FACT_ANCHORS];
    let mut acronym_mentions = 0u8;
    let mut max_anchor_repeats = 0u8;
    let mut negation_scope = 0u8;
    let mut post_anchor_negation_scope = 0u8;
    let mut pre_anchor_refutation_scope = 0u8;
    let mut prior_anchor_refutation_scope = 0u8;
    let mut last_anchor_age = u8::MAX;
    let mut last_entity_kind = 0u8;
    let mut last_entity_age = u8::MAX;
    let mut choice_left_kind = 0u8;
    let mut choice_scope = 0u8;
    let mut contrast_clause = false;
    let mut clause_novel_entity = false;
    let mut previous_end = 0usize;

    for token in TokenIter::new(answer) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_clause_boundary(answer, previous_end, start) {
            let strong_boundary = has_strong_clause_boundary(answer, previous_end, start);
            let retained_pre_anchor_refutation = if strong_boundary {
                0
            } else {
                pre_anchor_refutation_scope
            };
            let prior_clause_had_anchor = strong_boundary && last_anchor_age <= 3;
            negation_scope = 0;
            post_anchor_negation_scope = 0;
            pre_anchor_refutation_scope = retained_pre_anchor_refutation;
            prior_anchor_refutation_scope = if prior_clause_had_anchor { 4 } else { 0 };
            last_anchor_age = u8::MAX;
            last_entity_kind = 0;
            last_entity_age = u8::MAX;
            choice_left_kind = 0;
            choice_scope = 0;
            contrast_clause = false;
            clause_novel_entity = false;
        }
        previous_end = token.end;

        if is_fact_contrast(token.bytes) {
            negation_scope = 0;
            post_anchor_negation_scope = 0;
            pre_anchor_refutation_scope = 0;
            prior_anchor_refutation_scope = 0;
            last_anchor_age = u8::MAX;
            last_entity_kind = 0;
            last_entity_age = u8::MAX;
            choice_left_kind = 0;
            choice_scope = 0;
            contrast_clause = true;
            clause_novel_entity = false;
            continue;
        }
        if is_fact_affirmation(token.bytes) {
            pre_anchor_refutation_scope = 0;
            prior_anchor_refutation_scope = 0;
            last_anchor_age = last_anchor_age.saturating_add(1);
            last_entity_age = last_entity_age.saturating_add(1);
            continue;
        }
        if token_eq(token.bytes, b"only") && (negation_scope > 0 || post_anchor_negation_scope > 0)
        {
            negation_scope = 0;
            post_anchor_negation_scope = 0;
            continue;
        }
        if is_choice_connector(token.bytes) {
            choice_left_kind = if last_entity_age <= 3 {
                last_entity_kind
            } else {
                0
            };
            choice_scope = 5;
            last_anchor_age = last_anchor_age.saturating_add(1);
            last_entity_age = last_entity_age.saturating_add(1);
            continue;
        }
        if is_fact_negation(answer, token) {
            if last_anchor_age <= 3 {
                post_anchor_negation_scope = 6;
            } else {
                negation_scope = 6;
            }
            last_anchor_age = last_anchor_age.saturating_add(1);
            last_entity_age = last_entity_age.saturating_add(1);
            continue;
        }
        if is_fact_refutation(token.bytes) {
            if negation_scope > 0 || post_anchor_negation_scope > 0 {
                negation_scope = 0;
                pre_anchor_refutation_scope = 0;
            } else if last_anchor_age <= 3 || prior_anchor_refutation_scope > 0 {
                contradicted = true;
            } else {
                pre_anchor_refutation_scope = 5;
            }
            post_anchor_negation_scope = 0;
            prior_anchor_refutation_scope = 0;
            last_anchor_age = last_anchor_age.saturating_add(1);
            last_entity_age = last_entity_age.saturating_add(1);
            continue;
        }

        let hash = semantic_hash(token.bytes);
        if post_anchor_negation_scope > 0
            && (is_fact_relation_token(token.bytes)
                || hash.is_some_and(|value| question_tokens.contains(value)))
        {
            contradicted = true;
            post_anchor_negation_scope = 0;
        }
        let acronym_match = acronym_candidate
            && token_matches_fact_acronym(token.bytes, &anchors.acronym, anchors.acronym_len);
        let kind = if hash.is_some_and(|value| anchors.values.contains(value)) || acronym_match {
            1u8
        } else if hash.is_some_and(|value| {
            token_is_entity_like(token.bytes)
                && !question_tokens.contains(value)
                && !truth_tokens.contains(value)
                && !is_fact_relation_or_filler(token.bytes)
        }) {
            2u8
        } else {
            0u8
        };

        if kind == 1 {
            if acronym_match {
                acronym_mentions = acronym_mentions.saturating_add(1);
                max_anchor_repeats = max_anchor_repeats.max(acronym_mentions);
            } else if let Some(value) = hash {
                for (index, anchor) in anchors.values.values[..anchors.values.len]
                    .iter()
                    .enumerate()
                {
                    if *anchor == value {
                        anchor_counts[index] = anchor_counts[index].saturating_add(1);
                        max_anchor_repeats = max_anchor_repeats.max(anchor_counts[index]);
                        break;
                    }
                }
            }
            if negation_scope > 0 {
                contradicted = true;
            }
            if pre_anchor_refutation_scope > 0 {
                contradicted = true;
                pre_anchor_refutation_scope = 0;
            }
            last_anchor_age = 0;
        } else {
            last_anchor_age = last_anchor_age.saturating_add(1);
        }
        if kind == 2 {
            clause_novel_entity = true;
            if contrast_clause {
                novel_entity_after_contrast = true;
            }
        }
        if choice_scope > 0 && kind != 0 {
            if (choice_left_kind == 1 && kind == 2) || (choice_left_kind == 2 && kind == 1) {
                connector_ambiguity = true;
            }
            choice_left_kind = 0;
            choice_scope = 0;
        }
        if kind != 0 {
            last_entity_kind = kind;
            last_entity_age = 0;
        } else {
            last_entity_age = last_entity_age.saturating_add(1);
        }
        if hash.is_some_and(|value| question_tokens.contains(value))
            && clause_novel_entity
            && contrast_clause
        {
            novel_entity_after_contrast = true;
        }
        negation_scope = negation_scope.saturating_sub(1);
        post_anchor_negation_scope = post_anchor_negation_scope.saturating_sub(1);
        pre_anchor_refutation_scope = pre_anchor_refutation_scope.saturating_sub(1);
        prior_anchor_refutation_scope = prior_anchor_refutation_scope.saturating_sub(1);
        choice_scope = choice_scope.saturating_sub(1);
    }

    let expected_relation_pairs =
        fact_relation_pairs(ground_truth, &anchors.values, &question_tokens);
    let observed_relation_pairs = fact_relation_pairs(answer, &anchors.values, &question_tokens);
    let relation_pair_overlap = expected_relation_pairs.overlap(&observed_relation_pairs);
    let response_question_overlap = question_tokens.overlap(&response);
    let relation_mismatch = expected_relation_pairs.len > 0
        && relation_pair_overlap.saturating_mul(2) < expected_relation_pairs.len
        && ((overlap >= 2 && response_question_overlap >= 2)
            || (overlap >= 1 && novel_entity_after_contrast && response_question_overlap >= 1));

    let expected_directed_pairs =
        fact_directed_relation_pairs(ground_truth, &anchors.values, &question_entities);
    let observed_directed_pairs =
        fact_directed_relation_pairs(answer, &anchors.values, &question_entities);
    let directed_relation_overlap = expected_directed_pairs.overlap(&observed_directed_pairs);
    let unexpected_directed_relation = observed_directed_pairs.len > directed_relation_overlap;
    // A single multi-token name can legitimately produce several expected token-level pairs.
    let single_relation_alias = expected_directed_pairs.len > 1
        && observed_directed_pairs.len > 0
        && directed_relation_overlap > 0
        && !has_multiple_directed_fact_syntax(ground_truth, &anchors.values, &question_entities);
    let incomplete_multi_relation = expected_directed_pairs.len > 1
        && observed_directed_pairs.len > 0
        && directed_relation_overlap < expected_directed_pairs.len
        && !single_relation_alias;
    // Predicate-free replies have no edges, so compare canonical answer-entity spans instead.
    let incomplete_predicate_free_multi_answer = expected_directed_pairs.len > 1
        && observed_directed_pairs.len == 0
        && has_multiple_directed_fact_syntax(ground_truth, &anchors.values, &question_entities)
        && required_answer_entity_overlap > 0
        && required_answer_entity_overlap < required_answer_entities.len;
    let malformed_directed_claim = observed_directed_pairs.len == 0
        && has_malformed_connector_directed_claim(answer, &anchors.values, &question_entities);
    let directed_relation_mismatch = expected_directed_pairs.len > 0
        && (unexpected_directed_relation
            || incomplete_multi_relation
            || incomplete_predicate_free_multi_answer
            || malformed_directed_claim);
    let relation_mismatch = relation_mismatch
        && !(expected_directed_pairs.len > 0
            && expected_directed_pairs.len == observed_directed_pairs.len
            && directed_relation_overlap == expected_directed_pairs.len);

    let expected_entity_pairs = fact_entity_pairs(ground_truth, &anchors.values);
    let observed_entity_pairs = fact_entity_pairs(answer, &anchors.values);
    let entity_recombination = anchors.values.len >= 4
        && overlap == anchors.values.len
        && expected_entity_pairs.len > 0
        && observed_entity_pairs.len > 0
        && expected_entity_pairs.overlap(&observed_entity_pairs) == 0;

    let context_conflict = weather_question
        && anchors.context_constraints.len > 0
        && context_overlap == 0
        && novel_context_candidate_count(&question_tokens, &truth_tokens, answer) == 1;

    let support = if anchors.context_constraints.len == 0 {
        value_support.max(acronym_support)
    } else if context_conflict {
        0.0
    } else if context_overlap > 0 {
        if value_signal_present {
            (value_support.max(acronym_support) * 0.5) + 0.5
        } else {
            1.0
        }
    } else if value_signal_present {
        (value_support.max(acronym_support) * 0.5) + 0.25
    } else {
        0.5
    };

    Some(FactAnchorAssessment {
        support,
        contradicted,
        ambiguous_or_stuffed: connector_ambiguity
            || relation_mismatch
            || directed_relation_mismatch
            || entity_recombination
            || max_anchor_repeats > 3
            || context_conflict,
    })
}

fn numeric_set(text: &str) -> HashSet<MAX_NUMERIC_FACTS> {
    let mut set = HashSet::new();
    let bytes = text.as_bytes();
    let mut cursor = 0usize;

    while cursor < bytes.len() {
        let start = cursor;
        if matches!(bytes[cursor], b'+' | b'-') {
            let has_delimiter_before = cursor == 0
                || (!bytes[cursor - 1].is_ascii_alphanumeric() && bytes[cursor - 1] != b'.');
            if !has_delimiter_before || !bytes.get(cursor + 1).is_some_and(u8::is_ascii_digit) {
                cursor += 1;
                continue;
            }
            cursor += 1;
        } else if bytes[cursor].is_ascii_digit() {
            if cursor > 0 {
                let previous = bytes[cursor - 1];
                let signed_number_started_before = matches!(previous, b'+' | b'-')
                    && (cursor == 1
                        || (!bytes[cursor - 2].is_ascii_alphanumeric()
                            && bytes[cursor - 2] != b'.'));
                if previous.is_ascii_alphanumeric()
                    || previous == b'.'
                    || signed_number_started_before
                {
                    cursor += 1;
                    continue;
                }
            }
        } else {
            cursor += 1;
            continue;
        }

        let digits_start = cursor;
        while cursor < bytes.len() && bytes[cursor].is_ascii_digit() {
            cursor += 1;
        }
        if cursor == digits_start {
            cursor = start + 1;
            continue;
        }
        if bytes.get(cursor) == Some(&b'.') && bytes.get(cursor + 1).is_some_and(u8::is_ascii_digit)
        {
            cursor += 1;
            while cursor < bytes.len() && bytes[cursor].is_ascii_digit() {
                cursor += 1;
            }
        }

        let adjacent_to_time_separator =
            (start > 0 && bytes[start - 1] == b':') || bytes.get(cursor) == Some(&b':');
        let is_percentage = bytes.get(cursor) == Some(&b'%');
        if !adjacent_to_time_separator
            && !is_percentage
            && let Some(value) = parse_decimal(&bytes[start..cursor])
            && value.is_finite()
        {
            let normalized = if value == 0.0 { 0.0 } else { value };
            set.insert(normalized.to_bits() as u64);
        }
    }
    set
}

fn numeric_quality(ground_truth: &str, answer: &str) -> Option<f32> {
    let truth = numeric_set(ground_truth);
    if truth.len == 0 {
        return None;
    }
    let response = numeric_set(answer);
    if response.len == 0 {
        return Some(0.0);
    }
    Some(truth.overlap(&response) as f32 / truth.len as f32)
}

fn has_conflicting_numeric_facts(ground_truth: &str, answer: &str) -> bool {
    let truth = numeric_set(ground_truth);
    let response = numeric_set(answer);
    truth.len > 0 && response.len > 0 && truth.overlap(&response) == 0
}

fn concept_quality(ground_truth: &str, answer: &str) -> Option<f32> {
    let truth = weather_concept_mask(ground_truth);
    if truth == 0 {
        return None;
    }
    let response = weather_concept_mask(answer);
    if response == 0 {
        return Some(0.0);
    }
    let overlap = (truth & response).count_ones() as f32;
    let precision = overlap / response.count_ones() as f32;
    let recall = overlap / truth.count_ones() as f32;
    if precision + recall == 0.0 {
        Some(0.0)
    } else {
        Some((2.0 * precision * recall) / (precision + recall))
    }
}

fn is_weather_question(question: &str) -> bool {
    let mut weather_word = false;
    let mut forecast_word = false;
    let mut temporal_cue = false;
    for token in TokenIter::new(question) {
        let lowercase_word = !token_is_entity_like(token.bytes);
        weather_word |= lowercase_word && token_eq(token.bytes, b"weather");
        forecast_word |= lowercase_word && token_eq(token.bytes, b"forecast");
        temporal_cue |= token_eq(token.bytes, b"current")
            || token_eq(token.bytes, b"currently")
            || token_eq(token.bytes, b"hour")
            || token_eq(token.bytes, b"hours")
            || token_eq(token.bytes, b"later")
            || token_eq(token.bytes, b"now")
            || token_eq(token.bytes, b"today")
            || token_eq(token.bytes, b"tomorrow")
            || token_eq(token.bytes, b"tonight")
            || token_eq(token.bytes, b"utc");
    }
    let has_weather_concept = weather_concept_mask(question) != 0;
    weather_word
        || (forecast_word && temporal_cue)
        || (has_weather_concept && temporal_cue)
        || (has_weather_concept && is_binary_question(question))
}

fn question_requests_acronym(question: &str) -> bool {
    TokenIter::new(question).any(|token| {
        token_eq(token.bytes, b"abbreviation")
            || token_eq(token.bytes, b"abbreviated")
            || token_eq(token.bytes, b"acronym")
            || token_eq(token.bytes, b"initial")
            || token_eq(token.bytes, b"initials")
    })
}

fn is_binary_question(question: &str) -> bool {
    let Some(first) = TokenIter::new(question).next() else {
        return false;
    };
    const LEADING_AUXILIARIES: &[&[u8]] = &[
        b"am", b"are", b"can", b"could", b"did", b"do", b"does", b"had", b"has", b"have", b"is",
        b"may", b"might", b"must", b"shall", b"should", b"was", b"were", b"will", b"would",
    ];
    LEADING_AUXILIARIES
        .iter()
        .any(|auxiliary| token_eq(first.bytes, auxiliary))
}

fn factual_quality(
    ground_truth: &str,
    answer_text: &str,
    truth_polarity: Polarity,
    answer_polarity: Polarity,
    truth_probability: Option<f32>,
    answer_probability: Option<f32>,
    include_weather_concepts: bool,
) -> f32 {
    let mut total = 0.0f32;
    let mut signals = 0u8;

    if let Some(expected) = truth_probability {
        total += answer_probability
            .map(|actual| (1.0 - (expected - actual).abs()).clamp(0.0, 1.0))
            .unwrap_or(0.0);
        signals += 1;
    }

    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative) {
        total += if truth_polarity == answer_polarity {
            1.0
        } else {
            0.0
        };
        signals += 1;
    }

    if include_weather_concepts && let Some(concepts) = concept_quality(ground_truth, answer_text) {
        total += concepts;
        signals += 1;
    }
    if let Some(numbers) = numeric_quality(ground_truth, answer_text) {
        total += numbers;
        signals += 1;
    }

    if signals == 0 {
        0.5
    } else {
        total / signals as f32
    }
}

pub(crate) fn evaluate(
    question: &str,
    ground_truth_raw: &str,
    miner_answer_raw: &str,
) -> Evaluation {
    let mut issues = 0u32;
    if question.len() > MAX_QUESTION_BYTES
        || ground_truth_raw.len() > MAX_GROUND_TRUTH_BYTES
        || miner_answer_raw.len() > MAX_MINER_ANSWER_BYTES
    {
        issues |= ISSUE_INPUT_TOO_LONG;
    }
    if miner_answer_raw.trim().is_empty() {
        issues |= ISSUE_EMPTY_ANSWER;
    }
    if ground_truth_raw.trim().is_empty() {
        issues |= ISSUE_EMPTY_GROUND_TRUTH;
    }
    for text in [question, ground_truth_raw, miner_answer_raw] {
        if is_json_like(text) && !is_valid_json(text) {
            issues |= ISSUE_MALFORMED_JSON;
        }
    }
    if issues != 0 {
        return zero(issues);
    }

    let ground_truth = select_scoring_text(ground_truth_raw);
    let answer = select_scoring_text(miner_answer_raw);
    if ground_truth.trim().is_empty() {
        return zero(ISSUE_EMPTY_GROUND_TRUTH);
    }
    if answer.trim().is_empty() {
        return zero(ISSUE_EMPTY_ANSWER);
    }
    if has_time_outside_question(question, answer) {
        return zero(ISSUE_WRONG_TIME_WINDOW);
    }
    if is_keyword_stuffed(answer) {
        return zero(ISSUE_KEYWORD_STUFFING);
    }

    let truth_polarity = explicit_polarity(ground_truth);
    let explicit_answer_polarity = explicit_polarity(answer);
    if truth_polarity == Polarity::Contradictory {
        return zero(ISSUE_AMBIGUOUS_GROUND_TRUTH);
    }
    if explicit_answer_polarity == Polarity::Contradictory {
        return zero(ISSUE_CONTRADICTORY_POLARITY);
    }

    let truth_probability = probability(ground_truth_raw).or_else(|| probability(ground_truth));
    let answer_probability = probability(miner_answer_raw).or_else(|| probability(answer));
    if let Some(value) = answer_probability
        && matches!(
            explicit_answer_polarity,
            Polarity::Positive | Polarity::Negative
        )
    {
        let implied = if value >= 0.5 {
            Polarity::Positive
        } else {
            Polarity::Negative
        };
        if implied != explicit_answer_polarity {
            return zero(ISSUE_CONTRADICTORY_PROBABILITY);
        }
    }
    let answer_polarity = if explicit_answer_polarity == Polarity::Unknown {
        answer_probability.map_or(Polarity::Unknown, |value| {
            if value >= 0.5 {
                Polarity::Positive
            } else {
                Polarity::Negative
            }
        })
    } else {
        explicit_answer_polarity
    };

    let json_content_exact =
        is_json_like(miner_answer_raw) && decoded_json_content_eq(answer, ground_truth);
    if ground_truth.trim() == answer.trim()
        || ground_truth.trim().eq_ignore_ascii_case(answer.trim())
        || json_content_exact
    {
        return Evaluation { score: 1.0, issues };
    }

    let weather_question = is_weather_question(question);
    let fact_assessment = fact_anchor_assessment(question, ground_truth, answer, weather_question);
    if fact_assessment.is_some_and(|assessment| assessment.contradicted) {
        return zero(ISSUE_CONTRADICTORY_FACT_ANCHOR);
    }

    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && matches!(answer_polarity, Polarity::Positive | Polarity::Negative)
        && truth_polarity != answer_polarity
        && (fact_assessment.is_none() || is_binary_question(question))
    {
        return zero(ISSUE_POLARITY_MISMATCH);
    }

    let semantic_quality = semantic_f1(ground_truth, answer);
    let has_other_truth_signal = truth_polarity != Polarity::Unknown
        || truth_probability.is_some()
        || numeric_quality(ground_truth, answer).is_some();
    if semantic_quality.is_none() && !has_other_truth_signal {
        return zero(ISSUE_NO_SCORABLE_GROUND_TRUTH);
    }

    let scoring_truth_polarity = if fact_assessment.is_some() {
        Polarity::Unknown
    } else {
        truth_polarity
    };
    let scoring_answer_polarity = if fact_assessment.is_some() {
        Polarity::Unknown
    } else {
        answer_polarity
    };
    let factual = factual_quality(
        ground_truth,
        answer,
        scoring_truth_polarity,
        scoring_answer_polarity,
        truth_probability,
        answer_probability,
        fact_assessment.is_none() || weather_question,
    );
    let concision = if answer.len() <= 240 {
        1.0
    } else {
        1.0 - ((answer.len() - 240) as f32 / (MAX_MINER_ANSWER_BYTES - 240) as f32)
    }
    .clamp(0.0, 1.0);

    let effective_semantic = fact_assessment
        .map_or(semantic_quality.unwrap_or(0.5), |assessment| {
            (0.25 * semantic_quality.unwrap_or(0.0)) + (0.75 * assessment.support)
        });

    let mut score = (0.55 * effective_semantic) + (0.30 * factual) + (0.15 * concision);
    if fact_assessment.is_some_and(|assessment| assessment.ambiguous_or_stuffed) {
        issues |= ISSUE_AMBIGUOUS_FACT_ANCHORS;
        score = score.min(0.49);
    }
    if is_binary_question(question)
        && matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && answer_polarity == Polarity::Unknown
    {
        issues |= ISSUE_MISSING_BINARY_ANSWER;
        score = score.min(0.49);
    }
    if numeric_quality(ground_truth, answer) == Some(0.0) {
        score = score.min(if has_conflicting_numeric_facts(ground_truth, answer) {
            0.30
        } else {
            0.49
        });
    }
    if numeric_binding_conflict(ground_truth, answer) {
        score = score.min(0.49);
    }
    if let Some(expected_probability) = truth_probability {
        score = match answer_probability {
            Some(actual_probability) => {
                score.min((1.0 - (expected_probability - actual_probability).abs()).clamp(0.0, 1.0))
            }
            None => score.min(0.49),
        };
    }
    if !score.is_finite() {
        score = 0.0;
    }
    Evaluation {
        score: score.clamp(0.0, 1.0),
        issues,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const QUESTION: &str =
        "Will measurable precipitation > 0.1 mm occur in Lagos from 15:00 to 16:00 UTC?";

    #[test]
    fn correct_binary_weather_answers_score_high() {
        let evaluation = evaluate(
            QUESTION,
            "Yes. Measurable precipitation occurred in Lagos during the requested UTC hour.",
            "Yes — there is a 65% probability of measurable precipitation in Lagos during the requested UTC hour.",
        );
        assert!(evaluation.score >= 0.8, "{evaluation:?}");
        assert_eq!(evaluation.issues, 0);
    }

    #[test]
    fn punctuation_only_json_escaping_preserves_exact_content_score() {
        let evaluation = evaluate(
            "q",
            "Rain \"likely\".",
            r#"{"content":"Rain \"likely\".","metadata":"line one\nline two","probability":0.65}"#,
        );
        assert_eq!(evaluation.score, 1.0, "{evaluation:?}");
    }

    #[test]
    fn exact_matches_do_not_bypass_safety_gates() {
        let wrong_time = "Rain occurred from 17:00 to 18:00 UTC.";
        let wrong_time_evaluation = evaluate(
            "Did rain occur from 15:00 to 16:00 UTC?",
            wrong_time,
            wrong_time,
        );
        assert_eq!(
            wrong_time_evaluation.score, 0.0,
            "{wrong_time_evaluation:?}"
        );
        assert_ne!(wrong_time_evaluation.issues & ISSUE_WRONG_TIME_WINDOW, 0);

        let stuffed = "rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain";
        let stuffed_evaluation = evaluate("What happened?", stuffed, stuffed);
        assert_eq!(stuffed_evaluation.score, 0.0, "{stuffed_evaluation:?}");
        assert_ne!(stuffed_evaluation.issues & ISSUE_KEYWORD_STUFFING, 0);

        let contradictory = "Rain is likely with a 20% probability.";
        let contradictory_evaluation = evaluate("Will it rain?", contradictory, contradictory);
        assert_eq!(
            contradictory_evaluation.score, 0.0,
            "{contradictory_evaluation:?}"
        );
        assert_ne!(
            contradictory_evaluation.issues & ISSUE_CONTRADICTORY_PROBABILITY,
            0
        );
    }

    #[test]
    fn contradictions_and_wrong_windows_are_zero() {
        assert_eq!(
            evaluate(
                QUESTION,
                "Yes. Measurable precipitation occurred in Lagos during the requested UTC hour.",
                r#"{"content":"No. Measurable precipitation will not occur.","probability":0.9}"#,
            )
            .score,
            0.0
        );
        assert_eq!(
            evaluate(
                QUESTION,
                "Yes. Measurable precipitation occurred in Lagos during the requested UTC hour.",
                "Yes. Rain occurred from 10:00 to 11:00 UTC.",
            )
            .issues,
            ISSUE_WRONG_TIME_WINDOW
        );
    }

    #[test]
    fn generic_temperature_paraphrase_beats_wrong_forecast() {
        let question = "What will the maximum temperature and sky condition be in Lagos tomorrow?";
        let truth = "The maximum temperature in Lagos will be 31 C with partly cloudy conditions.";
        let good = evaluate(
            question,
            truth,
            "Lagos should reach a high near 31°C with some cloud cover.",
        );
        let wrong = evaluate(
            question,
            truth,
            "Lagos will have a low near 19 C with clear skies.",
        );
        assert!(good.score >= 0.55, "{good:?}");
        assert!(good.score > wrong.score, "good={good:?}, wrong={wrong:?}");
    }

    #[test]
    fn a_question_copy_without_a_binary_answer_is_capped() {
        let evaluation = evaluate(
            QUESTION,
            "No. Measurable precipitation did not occur in Lagos during the requested UTC hour.",
            "Will measurable precipitation occur in Lagos during the requested UTC hour?",
        );
        assert!(evaluation.score <= 0.49, "{evaluation:?}");
        assert_ne!(evaluation.issues & ISSUE_MISSING_BINARY_ANSWER, 0);
    }

    #[test]
    fn exact_bounds_are_enforced_in_utf8_bytes() {
        let at_limit = "x".repeat(MAX_MINER_ANSWER_BYTES);
        assert_eq!(
            evaluate("q", "x", &at_limit).issues & ISSUE_INPUT_TOO_LONG,
            0
        );
        let over_limit = "é".repeat((MAX_MINER_ANSWER_BYTES / 2) + 1);
        assert_ne!(
            evaluate("q", "é", &over_limit).issues & ISSUE_INPUT_TOO_LONG,
            0
        );
    }

    #[test]
    fn exact_negative_statements_and_negated_expectations_are_not_contradictory() {
        let exact = evaluate(
            "Will it rain?",
            "No rain is expected.",
            "No rain is expected.",
        );
        assert_eq!(exact.score, 1.0, "{exact:?}");
        assert_eq!(exact.issues, 0, "{exact:?}");

        let paraphrase = evaluate(
            "Will it rain?",
            "No rain is expected.",
            "Rain is not expected.",
        );
        assert!(paraphrase.score >= 0.8, "{paraphrase:?}");
        assert_eq!(paraphrase.issues, 0, "{paraphrase:?}");

        let contradictory = evaluate(
            "Will it rain?",
            "No rain is expected.",
            "No rain is expected, but yes, rain is likely.",
        );
        assert_eq!(contradictory.score, 0.0, "{contradictory:?}");
        assert_ne!(
            contradictory.issues & ISSUE_CONTRADICTORY_POLARITY,
            0,
            "{contradictory:?}"
        );
    }

    #[test]
    fn signed_numeric_facts_do_not_match_the_opposite_sign() {
        let question = "What will the minimum temperature be tomorrow?";
        let truth = "The minimum temperature will be -5 C.";
        let exact = evaluate(question, truth, "The minimum temperature will be -5 C.");
        let wrong_sign = evaluate(question, truth, "The minimum temperature will be 5 C.");
        assert_eq!(exact.score, 1.0, "{exact:?}");
        assert!(wrong_sign.score <= 0.49, "{wrong_sign:?}");
        assert!(
            exact.score > wrong_sign.score,
            "exact={exact:?}, wrong={wrong_sign:?}"
        );
    }

    #[test]
    fn probability_disagreement_is_scored_even_when_polarity_matches() {
        let question = "What is the probability that rain will occur tomorrow?";
        let truth = "Yes. Rain is expected with 90% probability.";
        let close = evaluate(
            question,
            truth,
            "Yes. Rain is expected with 85% probability.",
        );
        let distant = evaluate(
            question,
            truth,
            "Yes. Rain is expected with 51% probability.",
        );
        assert!(close.score >= 0.84, "{close:?}");
        assert!(distant.score <= 0.61, "{distant:?}");
        assert!(
            close.score > distant.score,
            "close={close:?}, distant={distant:?}"
        );
    }

    #[test]
    fn qualified_negation_does_not_reverse_the_main_forecast() {
        let question = "Will rain occur tomorrow?";
        let truth = "Yes. Rain is expected tomorrow.";
        let duration = evaluate(
            question,
            truth,
            "Rain is expected, although it won't last long.",
        );
        let uncertainty = evaluate(question, truth, "Rain is likely, but not certain.");
        assert!(duration.score >= 0.6, "{duration:?}");
        assert!(uncertainty.score >= 0.55, "{uncertainty:?}");
    }

    #[test]
    fn probability_cannot_hide_wrong_numeric_facts() {
        let temperature_question = "What temperature and rain probability are expected tomorrow?";
        let temperature_truth = "The temperature will be 31 C with a 90% rain probability.";
        let wrong_temperature = evaluate(
            temperature_question,
            temperature_truth,
            "The temperature will be 19 C with a 90% rain probability.",
        );
        assert!(wrong_temperature.score <= 0.49, "{wrong_temperature:?}");

        let signed_truth = "The minimum temperature will be -5 C with a 90% snow probability.";
        let wrong_sign = evaluate(
            temperature_question,
            signed_truth,
            "The minimum temperature will be 5 C with a 90% snow probability.",
        );
        assert!(wrong_sign.score <= 0.49, "{wrong_sign:?}");
    }

    #[test]
    fn factual_paraphrases_clear_a_robust_ordering_margin() {
        let cases = [
            (
                "What is the capital of France?",
                "The capital of France is Paris.",
                "Paris, a major European city, serves as the French national capital.",
                "Berlin is the capital of France.",
            ),
            (
                "Who wrote Hamlet?",
                "William Shakespeare wrote Hamlet.",
                "It was authored by Shakespeare.",
                "It was authored by Charles Dickens.",
            ),
            (
                "What language is spoken in Brazil?",
                "Portuguese is the official language of Brazil.",
                "People there speak Portuguese.",
                "People there speak Spanish.",
            ),
            (
                "What is the largest ocean on Earth?",
                "The Pacific Ocean is the largest ocean on Earth.",
                "That is the Pacific.",
                "That is the Atlantic.",
            ),
        ];
        for (question, truth, good_answer, bad_answer) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(
                good.score - bad.score >= 0.20,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
        }
    }

    #[test]
    fn factual_value_anchors_drop_grammatical_residue() {
        let question = "What currency is used in Japan?";
        let truth = "Japan uses the yen as its currency.";
        let anchors = fact_anchors(question, truth, false);
        let yen = semantic_hash(b"yen").expect("yen is a semantic token");

        assert_eq!(anchors.values.len, 1);
        assert!(anchors.values.contains(yen));

        let good =
            fact_anchor_assessment(question, truth, "The currency is the Japanese yen.", false)
                .expect("currency truth should retain a factual anchor");
        let bad = fact_anchor_assessment(
            question,
            truth,
            "Japan uses the euro as its currency.",
            false,
        )
        .expect("currency truth should retain a factual anchor");
        assert_eq!(good.support, 1.0);
        assert_eq!(bad.support, 0.0);
    }

    #[test]
    fn factual_anchor_contradictions_and_stuffing_are_not_rewarded() {
        let cases = [
            (
                "What is the capital of France?",
                "The capital of France is Paris.",
                "Paris is not the capital; Berlin is.",
            ),
            (
                "Who wrote Hamlet?",
                "William Shakespeare wrote Hamlet.",
                "Shakespeare did not write Hamlet; Dickens did.",
            ),
            (
                "What is the chemical symbol for gold?",
                "The chemical symbol for gold is Au.",
                "The symbol is not Au; it is Ag.",
            ),
        ];
        for (question, truth, answer) in cases {
            let evaluation = evaluate(question, truth, answer);
            assert_eq!(evaluation.score, 0.0, "{evaluation:?}");
            assert_ne!(evaluation.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
        }

        for answer in [
            "The answer could be Paris or Berlin.",
            "Both Paris and Berlin are possible answers.",
            "Paris Paris Paris Paris Paris is the answer.",
        ] {
            let evaluation = evaluate(
                "What is the capital of France?",
                "The capital of France is Paris.",
                answer,
            );
            assert!(evaluation.score <= 0.49, "{answer:?}: {evaluation:?}");
        }

        for answer in [
            "The capital is not Berlin but Paris.",
            "Paris, not Berlin, is the capital of France.",
        ] {
            let evaluation = evaluate(
                "What is the capital of France?",
                "The capital of France is Paris.",
                answer,
            );
            assert!(evaluation.score >= 0.6, "{answer:?}: {evaluation:?}");
            assert_eq!(evaluation.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
        }
    }

    #[test]
    fn factual_acronym_support_does_not_change_numeric_or_weather_paths() {
        let abbreviation = evaluate(
            "Which country is commonly abbreviated by its three-letter initials?",
            "The United States of America is commonly abbreviated USA.",
            "It is the USA.",
        );
        let unrelated = evaluate(
            "Which country is commonly abbreviated by its three-letter initials?",
            "The United States of America is commonly abbreviated USA.",
            "It is Canada.",
        );
        assert!(
            abbreviation.score - unrelated.score >= 0.15,
            "{abbreviation:?} {unrelated:?}"
        );
        let negated_abbreviation = evaluate(
            "Which country is commonly abbreviated by its three-letter initials?",
            "The United States of America is commonly abbreviated USA.",
            "It is not the USA; it is Canada.",
        );
        assert_eq!(negated_abbreviation.score, 0.0, "{negated_abbreviation:?}");

        let numeric = evaluate(
            "What year was the treaty signed?",
            "The treaty was signed in 1992.",
            "The treaty was signed in 1993.",
        );
        assert!(numeric.score <= 0.49, "{numeric:?}");

        let correct_weather = evaluate(
            "Which weather condition is expected tomorrow?",
            "Rain with a 90% probability is expected tomorrow.",
            "Rain with a 90% probability is expected tomorrow.",
        );
        let wrong_weather = evaluate(
            "Which weather condition is expected tomorrow?",
            "Rain with a 90% probability is expected tomorrow.",
            "Snow with a 90% probability is expected tomorrow.",
        );
        assert!(
            correct_weather.score - wrong_weather.score >= 0.15,
            "{correct_weather:?} {wrong_weather:?}"
        );
    }

    #[test]
    fn factual_wording_collisions_do_not_reverse_entity_ordering() {
        let cases = [
            (
                "Which mountain is the highest above sea level?",
                "Mount Everest is the highest peak above sea level.",
                "Everest.",
                "K2 is the highest peak above sea level.",
            ),
            (
                "Where did the Battle of Hastings occur?",
                "The Battle of Hastings occurred in England.",
                "It took place in England.",
                "The Battle of Hastings occurred in France.",
            ),
            (
                "Which country is landlocked in central Europe?",
                "Switzerland has no coastline.",
                "Switzerland is landlocked.",
                "Austria has no coastline.",
            ),
            (
                "Who sang Purple Rain?",
                "Prince sang Purple Rain.",
                "The artist was Prince.",
                "Michael Jackson sang Purple Rain.",
            ),
            (
                "Who wrote Snow Crash?",
                "Neal Stephenson wrote Snow Crash.",
                "The author was Neal Stephenson.",
                "William Gibson wrote Snow Crash.",
            ),
        ];
        for (question, truth, good_answer, bad_answer) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(
                good.score - bad.score >= 0.15,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
        }
    }

    #[test]
    fn heldout_factual_discrimination_probes_clear_the_reported_floor() {
        let cases = [
            (
                "Who wrote The Weather Makers?",
                "Tim Flannery wrote The Weather Makers.",
                "The author was Tim Flannery.",
                "Bill McKibben wrote The Weather Makers.",
                Some(0.49),
            ),
            (
                "Who prepared the economic forecast?",
                "The World Bank prepared the economic forecast.",
                "It was prepared by the World Bank.",
                "The IMF prepared the economic forecast.",
                Some(0.49),
            ),
            (
                "Who will play storm in the next X-Men film?",
                "Cynthia Erivo will play Storm.",
                "Cynthia Erivo.",
                "Halle Berry will play storm.",
                Some(0.49),
            ),
            (
                "Who founded Acme?",
                "William Henry Gates founded Acme.",
                "Bill Gates.",
                "WHG.",
                None,
            ),
            (
                "What color is the flag?",
                "The flag is blue.",
                "Blue.",
                "Blue is wrong; Red is correct.",
                Some(0.49),
            ),
            (
                "What weather is expected tomorrow in Lagos?",
                "Rain is expected tomorrow in Lagos.",
                "Lagos should have rain tomorrow.",
                "Abuja should have rain tomorrow.",
                Some(0.49),
            ),
            (
                "What interval?",
                "The interval is 5/10.",
                "The interval is 5/10.",
                "The interval is 5-10.",
                Some(0.49),
            ),
        ];

        for (question, truth, good_answer, bad_answer, maximum_bad_score) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(
                good.score - bad.score >= 0.15,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
            if let Some(maximum) = maximum_bad_score {
                assert!(
                    bad.score <= maximum,
                    "question={question:?}, bad={bad:?}, maximum={maximum}"
                );
            }
        }
    }

    #[test]
    fn directed_fact_relations_normalize_voice_and_reject_reversal() {
        let cases = [
            (
                "Who defeated Bob?",
                "Alice defeated Bob.",
                "Bob was defeated by Alice.",
                "Bob defeated Alice.",
            ),
            (
                "Who taught Alice?",
                "Bob taught Alice.",
                "Alice was taught by Bob.",
                "Alice taught Bob.",
            ),
            (
                "Who mentored Eli?",
                "Dana mentored Eli.",
                "Eli was mentored by Dana.",
                "Eli mentored Dana.",
            ),
            (
                "Who hired Omar?",
                "Priya hired Omar.",
                "Omar was hired by Priya.",
                "Omar hired Priya.",
            ),
            (
                "Who rescued Noah?",
                "Maya rescued Noah.",
                "Noah was rescued by Maya.",
                "Noah rescued Maya.",
            ),
            (
                "Who discovered europa?",
                "Livia discovered Europa.",
                "Europa was discovered by Livia.",
                "Europa discovered Livia.",
            ),
        ];
        for (question, truth, good_answer, bad_answer) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(good.score >= 0.75, "good={good:?}");
            assert!(bad.score <= 0.49, "bad={bad:?}");
            assert!(
                good.score - bad.score >= 0.25,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
            assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
            assert_ne!(bad.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }

        for valid in [
            evaluate(
                "Who married Leona?",
                "Leona married Darius.",
                "Darius was married to Leona.",
            ),
            evaluate(
                "Who defeated Bob?",
                "Alice defeated Bob.",
                "Alice defeated Bob by decision.",
            ),
        ] {
            assert!(valid.score >= 0.75, "{valid:?}");
            assert_eq!(valid.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }
    }

    #[test]
    fn directed_fact_relations_handle_parentheticals_coordination_and_mixed_reversals() {
        let relation_pairs = |question: &str, truth: &str, text: &str| {
            let anchors = fact_anchors(question, truth, false);
            let question_entities = question_entity_set(question, truth);
            fact_directed_relation_pairs(text, &anchors.values, &question_entities)
        };
        let alice = semantic_hash(b"alice").expect("Alice is a semantic token");
        let bob = semantic_hash(b"bob").expect("Bob is a semantic token");
        let carol = semantic_hash(b"carol").expect("Carol is a semantic token");
        let dave = semantic_hash(b"dave").expect("Dave is a semantic token");

        let parenthetical_question = "Who defeated Bob?";
        let parenthetical_truth = "Alice, the reigning champion, defeated Bob.";
        let parenthetical_good = "Bob was defeated by Alice.";
        let parenthetical_bad = "Bob defeated Alice.";
        let alice_defeated_bob = directed_fact_pair_hash(alice, bob);
        let bob_defeated_alice = directed_fact_pair_hash(bob, alice);
        for text in [parenthetical_truth, parenthetical_good] {
            let pairs = relation_pairs(parenthetical_question, parenthetical_truth, text);
            assert_eq!(pairs.len, 1, "text={text:?}");
            assert!(pairs.contains(alice_defeated_bob), "text={text:?}");
        }
        let parenthetical_bad_pairs = relation_pairs(
            parenthetical_question,
            parenthetical_truth,
            parenthetical_bad,
        );
        assert_eq!(parenthetical_bad_pairs.len, 1);
        assert!(parenthetical_bad_pairs.contains(bob_defeated_alice));

        let coordinated_question = "Who defeated Bob and Dave?";
        let coordinated_truth = "Alice defeated Bob and Carol defeated Dave.";
        let coordinated_good = "Bob was defeated by Alice and Dave was defeated by Carol.";
        let coordinated_swapped = "Bob was defeated by Carol and Dave was defeated by Alice.";
        let carol_defeated_dave = directed_fact_pair_hash(carol, dave);
        let carol_defeated_bob = directed_fact_pair_hash(carol, bob);
        let alice_defeated_dave = directed_fact_pair_hash(alice, dave);
        for text in [coordinated_truth, coordinated_good] {
            let pairs = relation_pairs(coordinated_question, coordinated_truth, text);
            assert_eq!(pairs.len, 2, "text={text:?}");
            assert!(pairs.contains(alice_defeated_bob), "text={text:?}");
            assert!(pairs.contains(carol_defeated_dave), "text={text:?}");
        }
        let coordinated_bad_pairs =
            relation_pairs(coordinated_question, coordinated_truth, coordinated_swapped);
        assert_eq!(coordinated_bad_pairs.len, 2);
        assert!(coordinated_bad_pairs.contains(carol_defeated_bob));
        assert!(coordinated_bad_pairs.contains(alice_defeated_dave));

        let mixed_bad = "Alice defeated Bob. Dave defeated Carol.";
        let mixed_bad_pairs = relation_pairs(coordinated_question, coordinated_truth, mixed_bad);
        assert_eq!(mixed_bad_pairs.len, 2);
        assert!(mixed_bad_pairs.contains(alice_defeated_bob));
        assert!(mixed_bad_pairs.contains(directed_fact_pair_hash(dave, carol)));

        for (question, truth, good_answer, bad_answer) in [
            (
                parenthetical_question,
                parenthetical_truth,
                parenthetical_good,
                parenthetical_bad,
            ),
            (
                coordinated_question,
                coordinated_truth,
                coordinated_good,
                coordinated_swapped,
            ),
            (
                coordinated_question,
                coordinated_truth,
                coordinated_good,
                mixed_bad,
            ),
        ] {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(good.score >= 0.75, "good={good:?}");
            assert!(bad.score <= 0.49, "bad={bad:?}");
            assert!(
                good.score - bad.score >= 0.25,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
            assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
            assert_ne!(bad.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }
    }

    #[test]
    fn directed_fact_relations_expand_shared_predicate_coordination() {
        let relation_pairs = |question: &str, truth: &str, text: &str| {
            let anchors = fact_anchors(question, truth, false);
            let question_entities = question_entity_set(question, truth);
            fact_directed_relation_pairs(text, &anchors.values, &question_entities)
        };
        let alice = semantic_hash(b"alice").expect("Alice is a semantic token");
        let bob = semantic_hash(b"bob").expect("Bob is a semantic token");
        let carol = semantic_hash(b"carol").expect("Carol is a semantic token");
        let dave = semantic_hash(b"dave").expect("Dave is a semantic token");
        let alice_defeated_bob = directed_fact_pair_hash(alice, bob);
        let alice_defeated_dave = directed_fact_pair_hash(alice, dave);

        let question = "Who did Alice defeat?";
        let truth = "Alice defeated Bob and Dave.";
        let complete_passive = "Bob and Dave were defeated by Alice.";
        let incomplete = "Alice defeated Bob.";
        let complete = evaluate(question, truth, complete_passive);
        let partial = evaluate(question, truth, incomplete);
        assert!(
            complete.score >= 0.75,
            "complete={complete:?}, partial={partial:?}"
        );
        assert!(partial.score <= 0.49, "partial={partial:?}");
        assert!(
            complete.score - partial.score >= 0.25,
            "complete={complete:?}, partial={partial:?}"
        );
        assert_eq!(complete.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(partial.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        for text in [
            truth,
            complete_passive,
            "Alice defeated Bob and defeated Dave.",
            "Alice defeated Bob and Alice defeated Dave.",
        ] {
            let pairs = relation_pairs(question, truth, text);
            assert_eq!(pairs.len, 2, "text={text:?}");
            assert!(pairs.contains(alice_defeated_bob), "text={text:?}");
            assert!(pairs.contains(alice_defeated_dave), "text={text:?}");
        }

        let shared_subject_question = "Who defeated Bob?";
        let shared_subject_truth = "Alice and Carol defeated Bob.";
        let shared_subject_pairs = relation_pairs(
            shared_subject_question,
            shared_subject_truth,
            shared_subject_truth,
        );
        assert_eq!(shared_subject_pairs.len, 2);
        assert!(shared_subject_pairs.contains(alice_defeated_bob));
        assert!(shared_subject_pairs.contains(directed_fact_pair_hash(carol, bob)));

        let shared_subject_good = evaluate(
            shared_subject_question,
            shared_subject_truth,
            "Bob was defeated by Alice and Carol.",
        );
        let shared_subject_incomplete = evaluate(
            shared_subject_question,
            shared_subject_truth,
            "Alice defeated Bob.",
        );
        assert!(
            shared_subject_good.score >= 0.75,
            "good={shared_subject_good:?}"
        );
        assert!(
            shared_subject_incomplete.score <= 0.49,
            "incomplete={shared_subject_incomplete:?}"
        );
        assert_eq!(shared_subject_good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(
            shared_subject_incomplete.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS,
            0
        );
    }

    #[test]
    fn directed_fact_relations_preserve_surname_aliases_and_require_complete_terse_answers() {
        let author_question = "Who wrote Hamlet?";
        let author_truth = "William Shakespeare wrote Hamlet.";
        for answer in [
            "Shakespeare wrote Hamlet.",
            "Hamlet was written by Shakespeare.",
        ] {
            let valid = evaluate(author_question, author_truth, answer);
            assert!(valid.score >= 0.75, "answer={answer:?}: {valid:?}");
            assert_eq!(valid.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }
        let wrong_author = evaluate(author_question, author_truth, "Dickens wrote Hamlet.");
        assert!(wrong_author.score <= 0.49, "{wrong_author:?}");
        assert_ne!(wrong_author.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        let writing_question = "Who wrote Hamlet and Oliver Twist?";
        let writing_truth = "Shakespeare wrote Hamlet, and Dickens wrote Oliver Twist.";
        let complete = evaluate(writing_question, writing_truth, "Shakespeare and Dickens.");
        let incomplete = evaluate(writing_question, writing_truth, "Shakespeare.");
        assert!(complete.score > 0.49, "complete={complete:?}");
        assert!(incomplete.score <= 0.49, "incomplete={incomplete:?}");
        assert_eq!(complete.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(incomplete.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        let full_name_truth =
            "William Shakespeare wrote Hamlet, and Charles Dickens wrote Oliver Twist.";
        let surname_pair = evaluate(
            writing_question,
            full_name_truth,
            "Shakespeare and Dickens.",
        );
        let one_surname = evaluate(writing_question, full_name_truth, "Shakespeare.");
        assert!(surname_pair.score > 0.49, "complete={surname_pair:?}");
        assert!(one_surname.score <= 0.49, "incomplete={one_surname:?}");
        assert_eq!(surname_pair.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(one_surname.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
    }

    #[test]
    fn directed_fact_relations_support_active_gapping() {
        let question = "Who wrote Hamlet and Oliver Twist?";
        let truth = "Shakespeare wrote Hamlet, and Dickens wrote Oliver Twist.";
        let gapped = "Shakespeare wrote Hamlet; Dickens, Oliver Twist.";
        let comma_gapped = "Shakespeare wrote Hamlet, and Dickens, Oliver Twist.";
        let wrong = "Shakespeare wrote Hamlet; Tolkien, Oliver Twist.";
        let comma_wrong = "Shakespeare wrote Hamlet, and Tolkien, Oliver Twist.";
        let anchors = fact_anchors(question, truth, false);
        let question_entities = question_entity_set(question, truth);
        let expected = fact_directed_relation_pairs(truth, &anchors.values, &question_entities);
        assert_eq!(expected.len, 2);
        for answer in [gapped, comma_gapped] {
            let observed =
                fact_directed_relation_pairs(answer, &anchors.values, &question_entities);
            assert_eq!(observed.len, 2, "answer={answer:?}");
            assert_eq!(expected.overlap(&observed), 2, "answer={answer:?}");
        }

        for (valid_answer, invalid_answer) in [(gapped, wrong), (comma_gapped, comma_wrong)] {
            let valid = evaluate(question, truth, valid_answer);
            let invalid = evaluate(question, truth, invalid_answer);
            assert!(valid.score >= 0.75, "answer={valid_answer:?}: {valid:?}");
            assert!(
                invalid.score <= 0.49,
                "answer={invalid_answer:?}: {invalid:?}"
            );
            assert_eq!(valid.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
            assert_ne!(invalid.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }
    }

    #[test]
    fn directed_fact_relations_ignore_subordinate_parenthetical_predicates() {
        let question = "Who defeated Bob?";
        let truth = "Alice, inspired by Carol, defeated Bob.";
        let good_answer = "Bob was defeated by Alice.";
        let bad_answer = "Bob defeated Alice.";
        let anchors = fact_anchors(question, truth, false);
        let question_entities = question_entity_set(question, truth);
        let alice = semantic_hash(b"alice").expect("Alice is a semantic token");
        let bob = semantic_hash(b"bob").expect("Bob is a semantic token");
        let expected = fact_directed_relation_pairs(truth, &anchors.values, &question_entities);
        assert_eq!(expected.len, 1);
        assert!(expected.contains(directed_fact_pair_hash(alice, bob)));

        let good = evaluate(question, truth, good_answer);
        let bad = evaluate(question, truth, bad_answer);
        assert!(good.score >= 0.75, "good={good:?}");
        assert!(bad.score <= 0.49, "bad={bad:?}");
        assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(bad.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
    }

    #[test]
    fn directed_fact_relations_reject_extra_novel_claims_after_punctuation() {
        let single_question = "Who defeated Bob?";
        let single_truth = "Alice defeated Bob.";
        let single_good = "Bob was defeated by Alice.";
        let single_extra = "Alice defeated Bob. Carol defeated Bob.";
        let good = evaluate(single_question, single_truth, single_good);
        let extra = evaluate(single_question, single_truth, single_extra);
        let leading_context = evaluate(
            single_question,
            single_truth,
            "Yesterday Alice defeated Bob.",
        );
        assert!(good.score >= 0.75, "good={good:?}");
        assert!(
            leading_context.score >= 0.75,
            "leading_context={leading_context:?}"
        );
        assert!(extra.score <= 0.49, "extra={extra:?}");
        assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_eq!(leading_context.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(extra.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        let writing_question = "Who wrote Hamlet and Oliver Twist?";
        let writing_truth = "Shakespeare wrote Hamlet, and Dickens wrote Oliver Twist.";
        let writing_good = "Hamlet was written by Shakespeare; Oliver Twist by Dickens.";
        let writing_extra =
            "Hamlet was written by Shakespeare; Oliver Twist by Dickens; Oliver Twist by Tolkien.";
        let complete = evaluate(writing_question, writing_truth, writing_good);
        let extra_claim = evaluate(writing_question, writing_truth, writing_extra);
        assert!(complete.score >= 0.75, "complete={complete:?}");
        assert!(extra_claim.score <= 0.49, "extra={extra_claim:?}");
        assert_eq!(complete.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(extra_claim.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
    }

    #[test]
    fn directed_fact_relations_reject_partial_and_elliptical_multi_fact_answers() {
        let coordinated_question = "Who defeated Bob and Dave?";
        let coordinated_truth = "Alice defeated Bob and Carol defeated Dave.";
        let coordinated_good = "Bob was defeated by Alice and Dave was defeated by Carol.";
        let coordinated_partial = "Bob was defeated by Alice.";

        let good = evaluate(coordinated_question, coordinated_truth, coordinated_good);
        let partial = evaluate(coordinated_question, coordinated_truth, coordinated_partial);
        assert!(good.score >= 0.75, "good={good:?}");
        assert!(partial.score <= 0.49, "partial={partial:?}");
        assert!(
            good.score - partial.score >= 0.25,
            "good={good:?}, partial={partial:?}"
        );
        assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(partial.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        let writing_question = "Who wrote Hamlet and Oliver Twist?";
        let writing_truth = "Shakespeare wrote Hamlet, and Dickens wrote Oliver Twist.";
        let writing_good = "Hamlet was written by Shakespeare; Oliver Twist by Dickens.";
        let writing_mixed = "Hamlet was written by Shakespeare; Oliver Twist by Shakespeare.";
        let terse_valid = "Shakespeare and Dickens.";

        let anchors = fact_anchors(writing_question, writing_truth, false);
        let question_entities = question_entity_set(writing_question, writing_truth);
        let expected_pairs =
            fact_directed_relation_pairs(writing_truth, &anchors.values, &question_entities);
        let good_pairs =
            fact_directed_relation_pairs(writing_good, &anchors.values, &question_entities);
        let mixed_pairs =
            fact_directed_relation_pairs(writing_mixed, &anchors.values, &question_entities);
        assert_eq!(expected_pairs.len, 2);
        assert_eq!(good_pairs.len, 2);
        assert_eq!(expected_pairs.overlap(&good_pairs), 2);
        assert_eq!(mixed_pairs.len, 2);
        assert_eq!(expected_pairs.overlap(&mixed_pairs), 1);

        let elliptical_good = evaluate(writing_question, writing_truth, writing_good);
        let elliptical_mixed = evaluate(writing_question, writing_truth, writing_mixed);
        assert!(elliptical_good.score >= 0.75, "good={elliptical_good:?}");
        assert!(elliptical_mixed.score <= 0.49, "bad={elliptical_mixed:?}");
        assert!(
            elliptical_good.score - elliptical_mixed.score >= 0.25,
            "good={elliptical_good:?}, bad={elliptical_mixed:?}"
        );
        assert_eq!(elliptical_good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        assert_ne!(elliptical_mixed.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);

        let terse = evaluate(writing_question, writing_truth, terse_valid);
        assert!(terse.score > 0.49, "terse={terse:?}");
        assert_eq!(terse.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
    }

    #[test]
    fn factual_refutations_bind_across_introductory_and_terminal_punctuation() {
        let question = "What color is the flag?";
        let truth = "The flag is blue.";
        let good = evaluate(question, truth, "Blue.");
        assert!(good.score >= 0.75, "{good:?}");

        for answer in [
            "Wrong: blue. Correct: red.",
            "Wrong, blue. Correct: red.",
            "Incorrect answer: blue; correct answer: red.",
            "Blue? False. Red is correct.",
        ] {
            let bad = evaluate(question, truth, answer);
            assert_eq!(bad.score, 0.0, "answer={answer:?}: {bad:?}");
            assert_ne!(bad.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
        }

        for answer in ["Wrong: red. Correct: blue.", "Not wrong: blue."] {
            let valid = evaluate(question, truth, answer);
            assert!(valid.score >= 0.7, "answer={answer:?}: {valid:?}");
            assert_eq!(valid.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
        }
    }

    #[test]
    fn weather_context_allows_omission_and_sources_but_rejects_replacements() {
        let question = "What weather is expected tomorrow in Lagos?";
        let truth = "Rain is expected tomorrow in Lagos.";
        for answer in [
            "Rain is expected tomorrow.",
            "Expect showers tomorrow.",
            "Lagos should have rain tomorrow according to ECMWF.",
            "ECMWF expects rain tomorrow.",
            "Meteoblue reports rain tomorrow.",
            "Rain tomorrow according to the Met Office.",
        ] {
            let good = evaluate(question, truth, answer);
            assert!(good.score >= 0.7, "answer={answer:?}: {good:?}");
            assert_eq!(good.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }

        let omitted = evaluate(question, truth, "Rain is expected tomorrow.");
        for answer in [
            "Rain is expected tomorrow in Abuja.",
            "Rain is expected tomorrow in abuja.",
            "Rain is expected tomorrow in the city of abuja.",
        ] {
            let bad = evaluate(question, truth, answer);
            assert!(bad.score <= 0.49, "answer={answer:?}: {bad:?}");
            assert!(
                omitted.score - bad.score >= 0.15,
                "good={omitted:?}, answer={answer:?}, bad={bad:?}"
            );
            assert_ne!(bad.issues & ISSUE_AMBIGUOUS_FACT_ANCHORS, 0);
        }
    }

    #[test]
    fn factual_negation_and_ambiguity_are_clause_aware() {
        let good = evaluate(
            "What is the capital of France?",
            "The capital of France is Paris.",
            "Paris is France's capital.",
        );
        for answer in [
            "Paris is not actually France's capital; Berlin is.",
            "Paris and Berlin are capitals of France.",
            "Paris is mentioned, but Berlin is France's capital.",
        ] {
            let bad = evaluate(
                "What is the capital of France?",
                "The capital of France is Paris.",
                answer,
            );
            assert!(
                good.score - bad.score >= 0.15,
                "answer={answer:?}, good={good:?}, bad={bad:?}"
            );
        }

        for answer in [
            "Paris is not a country; it is France's capital.",
            "Paris is both France's capital and its most populous city.",
            "The capital is not Berlin but Paris.",
        ] {
            let valid = evaluate(
                "What is the capital of France?",
                "The capital of France is Paris.",
                answer,
            );
            assert!(valid.score >= 0.6, "answer={answer:?}: {valid:?}");
            assert_eq!(valid.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
        }
    }

    #[test]
    fn factual_acronyms_require_uppercase_phrase_evidence() {
        let who_truth = "The World Health Organization coordinates international public health.";
        let (who_acronym, who_len) = fact_anchor_acronym(who_truth);
        assert_eq!(&who_acronym[..who_len], b"who");
        let who_assessment = fact_anchor_assessment(
            "Which organization coordinates international public health?",
            who_truth,
            "WHO.",
            false,
        )
        .expect("WHO truth should retain a factual anchor");
        assert_eq!(who_assessment.support, 0.5);

        let cases = [
            (
                "Which agency runs the United States civilian space program?",
                "The National Aeronautics and Space Administration runs the civilian space program.",
                "NASA.",
                "Canada.",
            ),
            (
                "Which organization coordinates international public health?",
                "The World Health Organization coordinates international public health.",
                "WHO.",
                "I wonder who knows.",
            ),
            (
                "Which city is also called the Big Apple?",
                "New York City is also called the Big Apple.",
                "NYC.",
                "Los Angeles.",
            ),
        ];
        for (question, truth, good_answer, bad_answer) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(
                good.score - bad.score >= 0.15,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
        }

        let normal = evaluate(
            "Which country is commonly abbreviated by its three-letter initials?",
            "The United States of America is commonly abbreviated USA.",
            "USA.",
        );
        let repeated = evaluate(
            "Which country is commonly abbreviated by its three-letter initials?",
            "The United States of America is commonly abbreviated USA.",
            "USA USA USA USA USA.",
        );
        assert!(
            normal.score - repeated.score >= 0.15,
            "{normal:?} {repeated:?}"
        );
    }

    #[test]
    fn factual_relation_bindings_resist_swapped_entities() {
        let cases = [
            (
                "Who wrote Hamlet and Oliver Twist?",
                "Shakespeare wrote Hamlet, and Dickens wrote Oliver Twist.",
                "Hamlet was written by Shakespeare; Oliver Twist by Dickens.",
                "Hamlet was written by Dickens; Oliver Twist by Shakespeare.",
            ),
            (
                "What are the first and last names of Microsoft's two founders?",
                "Microsoft was founded by Bill Gates and Paul Allen.",
                "Bill Gates and Paul Allen.",
                "Bill Allen and Paul Gates.",
            ),
        ];
        for (question, truth, good_answer, bad_answer) in cases {
            let good = evaluate(question, truth, good_answer);
            let bad = evaluate(question, truth, bad_answer);
            assert!(
                good.score - bad.score >= 0.15,
                "question={question:?}, good={good:?}, bad={bad:?}"
            );
        }
    }

    #[test]
    fn terse_entities_survive_verbose_ground_truth() {
        let good = evaluate(
            "What is the capital of France?",
            "Paris, a beautiful European metropolis, is France's national capital.",
            "Paris.",
        );
        let bad = evaluate(
            "What is the capital of France?",
            "Paris, a beautiful European metropolis, is France's national capital.",
            "Berlin.",
        );
        assert!(good.score - bad.score >= 0.15, "{good:?} {bad:?}");
    }

    #[test]
    fn stage_two_relation_revision_preserves_predicate_identity_and_orientation() {
        let relation_pairs = |question: &str, truth: &str, text: &str| {
            let anchors = fact_anchors(question, truth, false);
            let question_entities = question_entity_set(question, truth);
            fact_directed_relation_pairs(text, &anchors.values, &question_entities)
        };

        let teaching_question = "Who taught Eli?";
        let teaching_truth = "Dana taught Eli.";
        let teaching_good = "Eli learned from Dana.";
        let teaching_bad = "Dana learned from Eli.";
        let dana = semantic_hash(b"dana").expect("Dana is a semantic token");
        let eli = semantic_hash(b"eli").expect("Eli is a semantic token");
        let taught = directed_fact_relation_hash(FACT_RELATION_TEACH, dana, eli);
        assert!(relation_pairs(teaching_question, teaching_truth, teaching_good).contains(taught));
        assert!(!relation_pairs(teaching_question, teaching_truth, teaching_bad).contains(taught));
        let teaching_good_score = evaluate(teaching_question, teaching_truth, teaching_good);
        let teaching_bad_score = evaluate(teaching_question, teaching_truth, teaching_bad);
        let teaching_missing_connector =
            evaluate(teaching_question, teaching_truth, "Eli learned Dana.");
        let teaching_wrong_connector =
            evaluate(teaching_question, teaching_truth, "Eli learned to Dana.");
        assert!(teaching_good_score.score >= 0.75, "{teaching_good_score:?}");
        assert!(teaching_bad_score.score <= 0.49, "{teaching_bad_score:?}");
        assert!(
            teaching_missing_connector.score <= 0.49,
            "{teaching_missing_connector:?}"
        );
        assert!(
            teaching_wrong_connector.score <= 0.49,
            "{teaching_wrong_connector:?}"
        );

        let outcome_question = "Who defeated Bob?";
        let outcome_truth = "Alice defeated Bob.";
        let outcome_good = [
            "Alice overcame Bob.",
            "Alice won against Bob.",
            "Bob lost to Alice.",
        ];
        let outcome_bad = [
            "Bob overcame Alice.",
            "Bob won against Alice.",
            "Alice lost to Bob.",
        ];
        for (good, bad) in outcome_good.into_iter().zip(outcome_bad) {
            let good_score = evaluate(outcome_question, outcome_truth, good);
            let bad_score = evaluate(outcome_question, outcome_truth, bad);
            assert!(good_score.score >= 0.75, "good={good:?}: {good_score:?}");
            assert!(bad_score.score <= 0.49, "bad={bad:?}: {bad_score:?}");
        }
        for invalid in [
            "Alice won Bob.",
            "Alice won with Bob.",
            "Bob lost Alice.",
            "Bob lost from Alice.",
        ] {
            let invalid_score = evaluate(outcome_question, outcome_truth, invalid);
            assert!(
                invalid_score.score <= 0.49,
                "invalid={invalid:?}: {invalid_score:?}"
            );
        }

        let author_question = "Who wrote Hamlet?";
        let author_truth = "Alice wrote Hamlet.";
        let author_good = evaluate(
            author_question,
            author_truth,
            "Hamlet was authored by Alice.",
        );
        let author_bad = evaluate(author_question, author_truth, "Alice painted Hamlet.");
        assert!(author_good.score >= 0.75, "{author_good:?}");
        assert!(author_bad.score <= 0.49, "{author_bad:?}");

        let unknown_truth = "Alice admired Bob.";
        let unknown_question = "Who admired Bob?";
        let unknown_good = evaluate(unknown_question, unknown_truth, "Bob was admired by Alice.");
        let unknown_bad = evaluate(
            unknown_question,
            unknown_truth,
            "Bob was inspired by Alice.",
        );
        let present_good = evaluate(unknown_question, unknown_truth, "Alice admires Bob.");
        let present_bad = evaluate(unknown_question, unknown_truth, "Bob admires Alice.");
        assert!(unknown_good.score >= 0.75, "{unknown_good:?}");
        assert!(unknown_bad.score <= 0.49, "{unknown_bad:?}");
        assert!(present_good.score >= 0.75, "{present_good:?}");
        assert!(present_bad.score <= 0.49, "{present_bad:?}");

        let founded_question = "Who founded Acme?";
        let founded_truth = "Alice founded Acme.";
        let founded_good = evaluate(founded_question, founded_truth, "Alice founds Acme.");
        let founded_bad = evaluate(founded_question, founded_truth, "Alice found Acme.");
        assert!(founded_good.score >= 0.75, "{founded_good:?}");
        assert!(founded_bad.score <= 0.49, "{founded_bad:?}");

        for negated in [
            "Alice won't win against Bob.",
            "Alice won’t win against Bob.",
        ] {
            let negated_score = evaluate(outcome_question, outcome_truth, negated);
            assert!(
                negated_score.score <= 0.49,
                "{negated:?}: {negated_score:?}"
            );
        }
    }

    #[test]
    fn stage_two_relation_revision_handles_explicit_continuation_passive_ellipsis_and_three_gaps() {
        let active_question = "Who did Alice defeat?";
        let active_truth = "Alice defeated Bob and Dave.";
        let active_good = "Alice defeated Bob; Alice also defeated Dave.";
        let active_bad = "Alice defeated Bob; he defeated Dave.";
        let active_good_score = evaluate(active_question, active_truth, active_good);
        let active_bad_score = evaluate(active_question, active_truth, active_bad);
        assert!(active_good_score.score >= 0.75, "{active_good_score:?}");
        assert!(active_bad_score.score <= 0.49, "{active_bad_score:?}");

        let passive_question = "Who did Alice defeat?";
        let passive_truth = "Alice defeated Bob and Dave.";
        let passive_good = "Bob was defeated by Alice, and so was Dave.";
        let passive_bad = "Bob was defeated by Alice.";
        let passive_good_score = evaluate(passive_question, passive_truth, passive_good);
        let passive_bad_score = evaluate(passive_question, passive_truth, passive_bad);
        assert!(passive_good_score.score >= 0.75, "{passive_good_score:?}");
        assert!(passive_bad_score.score <= 0.49, "{passive_bad_score:?}");

        let gapping_question = "Who wrote Hamlet, Oliver Twist, and Pride and Prejudice?";
        let gapping_truth = "Shakespeare wrote Hamlet; Dickens wrote Oliver Twist; Austen wrote Pride and Prejudice.";
        let gapping_good =
            "Shakespeare wrote Hamlet; Dickens, Oliver Twist; Austen, Pride and Prejudice.";
        let gapping_bad =
            "Shakespeare wrote Hamlet; Dickens, Oliver Twist; Tolkien, Pride and Prejudice.";
        let terminal_gapping =
            "Shakespeare wrote Hamlet. Dickens, Oliver Twist. Austen, Pride and Prejudice.";
        let gapping_good_score = evaluate(gapping_question, gapping_truth, gapping_good);
        let gapping_bad_score = evaluate(gapping_question, gapping_truth, gapping_bad);
        let terminal_gapping_score = evaluate(gapping_question, gapping_truth, terminal_gapping);
        assert!(gapping_good_score.score >= 0.75, "{gapping_good_score:?}");
        assert!(gapping_bad_score.score <= 0.49, "{gapping_bad_score:?}");
        assert!(
            terminal_gapping_score.score <= 0.49,
            "{terminal_gapping_score:?}"
        );

        let stale_subject_question = "Who defeated Bob and Dave?";
        let stale_subject_truth = "Alice defeated Bob. Carol defeated Dave.";
        let stale_subject_good = "Alice defeated Bob. Carol defeated Dave.";
        let stale_subject_bad = "Alice defeated Bob. Carol said she defeated Dave.";
        let stale_subject_good_score = evaluate(
            stale_subject_question,
            stale_subject_truth,
            stale_subject_good,
        );
        let stale_subject_bad_score = evaluate(
            stale_subject_question,
            stale_subject_truth,
            stale_subject_bad,
        );
        assert!(
            stale_subject_good_score.score >= 0.75,
            "{stale_subject_good_score:?}"
        );
        assert!(
            stale_subject_bad_score.score <= 0.49,
            "{stale_subject_bad_score:?}"
        );

        let bare_by_question = "Who wrote Hamlet and Oliver Twist?";
        let bare_by_truth = "Shakespeare wrote Hamlet; Dickens wrote Oliver Twist.";
        let bare_by_good = "Hamlet was written by Shakespeare; Oliver Twist by Dickens.";
        let bare_by_good_score = evaluate(bare_by_question, bare_by_truth, bare_by_good);
        assert!(bare_by_good_score.score >= 0.75, "{bare_by_good_score:?}");
        let bare_by_coordinated_bad =
            "Shakespeare wrote Hamlet; Oliver Twist and Hamlet by Dickens.";
        let bare_by_coordinated_bad_score =
            evaluate(bare_by_question, bare_by_truth, bare_by_coordinated_bad);
        assert!(
            bare_by_coordinated_bad_score.score <= 0.49,
            "{bare_by_coordinated_bad_score:?}"
        );
        let bare_by_anchors = fact_anchors(bare_by_question, bare_by_truth, false);
        let bare_by_question_entities = question_entity_set(bare_by_question, bare_by_truth);
        let bare_by_coordinated_pairs = fact_directed_relation_pairs(
            bare_by_coordinated_bad,
            &bare_by_anchors.values,
            &bare_by_question_entities,
        );
        let dickens = semantic_hash(b"dickens").expect("Dickens is a semantic token");
        let oliver = semantic_hash(b"oliver").expect("Oliver is a semantic token");
        let twist = semantic_hash(b"twist").expect("Twist is a semantic token");
        let hamlet = semantic_hash(b"hamlet").expect("Hamlet is a semantic token");
        assert!(
            bare_by_coordinated_pairs.contains(directed_fact_relation_hash(
                FACT_RELATION_AUTHOR,
                dickens,
                oliver,
            ))
        );
        assert!(
            !bare_by_coordinated_pairs.contains(directed_fact_relation_hash(
                FACT_RELATION_AUTHOR,
                dickens,
                twist,
            ))
        );
        assert!(
            bare_by_coordinated_pairs.contains(directed_fact_relation_hash(
                FACT_RELATION_AUTHOR,
                dickens,
                hamlet,
            ))
        );

        let conjoined_title_question = "Who wrote Hamlet and Pride and Prejudice?";
        let conjoined_title_truth = "Shakespeare wrote Hamlet; Austen wrote Pride and Prejudice.";
        let conjoined_title_good =
            "Hamlet was written by Shakespeare; Pride and Prejudice by Austen.";
        let conjoined_title_bad =
            "Hamlet was written by Shakespeare; Pride and Prejudice by Shakespeare.";
        let conjoined_title_good_score = evaluate(
            conjoined_title_question,
            conjoined_title_truth,
            conjoined_title_good,
        );
        let conjoined_title_bad_score = evaluate(
            conjoined_title_question,
            conjoined_title_truth,
            conjoined_title_bad,
        );
        assert!(
            conjoined_title_good_score.score >= 0.75,
            "{conjoined_title_good_score:?}"
        );
        assert!(
            conjoined_title_bad_score.score <= 0.49,
            "{conjoined_title_bad_score:?}"
        );
        assert!(
            conjoined_title_good_score.score - conjoined_title_bad_score.score >= 0.15,
            "good={conjoined_title_good_score:?}, bad={conjoined_title_bad_score:?}"
        );
        let conjoined_title_anchors =
            fact_anchors(conjoined_title_question, conjoined_title_truth, false);
        let conjoined_title_question_entities =
            question_entity_set(conjoined_title_question, conjoined_title_truth);
        let conjoined_title_pairs = fact_directed_relation_pairs(
            conjoined_title_good,
            &conjoined_title_anchors.values,
            &conjoined_title_question_entities,
        );
        let austen = semantic_hash(b"austen").expect("Austen is a semantic token");
        let pride = semantic_hash(b"pride").expect("Pride is a semantic token");
        let prejudice = semantic_hash(b"prejudice").expect("Prejudice is a semantic token");
        assert!(conjoined_title_pairs.contains(directed_fact_relation_hash(
            FACT_RELATION_AUTHOR,
            austen,
            pride,
        )));
        assert!(conjoined_title_pairs.contains(directed_fact_relation_hash(
            FACT_RELATION_AUTHOR,
            austen,
            prejudice,
        )));

        for stale in [
            "Shakespeare wrote Hamlet; Dickens painted; Oliver Twist by Dickens.",
            "Shakespeare wrote Hamlet; Dickens was ready; Oliver Twist by Dickens.",
            "Shakespeare wrote Hamlet; Tolkien; Oliver Twist by Dickens.",
            "Shakespeare wrote Hamlet; he paused; Oliver Twist by Dickens.",
            "Shakespeare wrote Hamlet; Oliver Twist was read by Dickens.",
            "Shakespeare wrote Hamlet; Oliver Twist stood by Dickens.",
        ] {
            let stale_score = evaluate(bare_by_question, bare_by_truth, stale);
            assert!(stale_score.score <= 0.49, "{stale:?}: {stale_score:?}");
        }
    }

    #[test]
    fn stage_two_relation_revision_uses_surname_before_junior_suffix() {
        let question = "Who led the march?";
        let truth = "Martin Luther King Jr. led the march.";
        let good = evaluate(question, truth, "King led the march.");
        let bad = evaluate(question, truth, "Malcolm led the march.");
        let conflicting_full = evaluate(question, truth, "Martin Luther King Sr. led the march.");
        let conflicting_short = evaluate(question, truth, "King Sr. led the march.");
        let (acronym, acronym_len) = fact_anchor_acronym(truth);
        assert!(good.score >= 0.75, "good={good:?}");
        assert!(bad.score <= 0.49, "bad={bad:?}");
        assert!(conflicting_full.score <= 0.49, "{conflicting_full:?}");
        assert!(conflicting_short.score <= 0.49, "{conflicting_short:?}");
        assert!(good.score - bad.score >= 0.15, "good={good:?}, bad={bad:?}");
        assert_eq!(&acronym[..acronym_len], b"mlk");
    }
}
