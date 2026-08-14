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

fn factual_quality(
    ground_truth: &str,
    answer_text: &str,
    truth_polarity: Polarity,
    answer_polarity: Polarity,
    truth_probability: Option<f32>,
    answer_probability: Option<f32>,
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

    if let Some(concepts) = concept_quality(ground_truth, answer_text) {
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
    if ground_truth.trim() == answer.trim()
        || ground_truth.trim().eq_ignore_ascii_case(answer.trim())
    {
        return Evaluation { score: 1.0, issues };
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

    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && matches!(answer_polarity, Polarity::Positive | Polarity::Negative)
        && truth_polarity != answer_polarity
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

    let factual = factual_quality(
        ground_truth,
        answer,
        truth_polarity,
        answer_polarity,
        truth_probability,
        answer_probability,
    );
    let concision = if answer.len() <= 240 {
        1.0
    } else {
        1.0 - ((answer.len() - 240) as f32 / (MAX_MINER_ANSWER_BYTES - 240) as f32)
    }
    .clamp(0.0, 1.0);

    let mut score =
        (0.55 * semantic_quality.unwrap_or(0.5)) + (0.30 * factual) + (0.15 * concision);
    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && answer_polarity == Polarity::Unknown
    {
        issues |= ISSUE_MISSING_BINARY_ANSWER;
        score = score.min(0.49);
    }
    if numeric_quality(ground_truth, answer) == Some(0.0) {
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
}
