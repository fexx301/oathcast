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
pub(crate) const ISSUE_RANK_MODIFIER_CONFLICT: u32 = 1 << 14;
pub(crate) const ISSUE_ROLE_BINDING_REVERSED: u32 = 1 << 15;
pub(crate) const ISSUE_TREND_CONTRADICTION: u32 = 1 << 16;
pub(crate) const ISSUE_ROLE_BINDING_RECOMBINED: u32 = 1 << 17;
pub(crate) const ISSUE_CLAUSE_CONTRADICTION: u32 = 1 << 18;
pub(crate) const ISSUE_LIKELIHOOD_CONTRADICTION: u32 = 1 << 19;
pub(crate) const ISSUE_WINDOW_END_ONLY: u32 = 1 << 20;
pub(crate) const ISSUE_CONTEXT_ENTITY_SUBSTITUTED: u32 = 1 << 21;
pub(crate) const ISSUE_SPAN_WIDER_THAN_WINDOW: u32 = 1 << 22;
pub(crate) const ISSUE_UNSUPPORTED_YEAR: u32 = 1 << 23;
pub(crate) const ISSUE_PROBABILITY_DISAGREEMENT: u32 = 1 << 24;
pub(crate) const ISSUE_ANSWERS_NOTHING: u32 = 1 << 25;
pub(crate) const ISSUE_UNIT_SCALE_CONFLICT: u32 = 1 << 26;
pub(crate) const ISSUE_UNFALSIFIABLE_ANSWER: u32 = 1 << 27;

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

/// True when any negation appears anywhere in the text.
///
/// Needed because polarity is read in a single forward pass while English routinely puts
/// the negation after the subject. "Rain is not expected." names a weather concept first,
/// so treating that concept as an affirmative set a positive reading before the "not" was
/// reached, and the two together produced Unknown where the answer plainly means Negative.
fn text_has_negation(text: &str) -> bool {
    TokenIter::new(text).any(|token| {
        token_eq(token.bytes, b"no")
            || token_eq(token.bytes, b"not")
            || token_eq(token.bytes, b"never")
            || token_eq(token.bytes, b"cannot")
            || token_eq(token.bytes, b"without")
            || token_eq(token.bytes, b"neither")
            || token_eq(token.bytes, b"nor")
            || token_eq(token.bytes, b"none")
            || token_eq(token.bytes, b"absent")
            || token_eq(token.bytes, b"nil")
            || token_eq(token.bytes, b"dry")
            || token_eq(token.bytes, b"hardly")
            || token_eq(token.bytes, b"scarcely")
            || token_eq(token.bytes, b"lacking")
            || token_eq(token.bytes, b"devoid")
            || token_eq(token.bytes, b"failed")
            || token_eq(token.bytes, b"unlikely")
            || is_negative_contraction(text, token)
    })
}

fn explicit_polarity(text: &str, weather_question: bool) -> Polarity {
    // An interrogative asserts nothing. Without this, the fixture's question-copy case,
    // "Will measurable precipitation occur in Lagos during the requested UTC hour?", read as
    // a positive answer purely because it names precipitation, and so stopped tripping the
    // missing-binary-answer ceiling it is supposed to trip.
    if text.trim_end().ends_with('?') {
        return Polarity::Unknown;
    }
    // A weather concept counts as an affirmative only in text that negates nothing.
    let concepts_imply_polarity = weather_question && !text_has_negation(text);
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

        // "without", "neither" and their relatives negate as firmly as "not", and their
        // absence here was measured. Against the ground truth "No. Measurable
        // precipitation did not occur in Lagos during the requested UTC hour.", the
        // correct paraphrase "The hour passed without any measurable rainfall in Lagos."
        // came back Unknown rather than Negative, so it matched the truth's polarity no
        // better than the wrong "Lagos saw steady measurable rain", and the two scored
        // 0.4869 and 0.4863. The current champion separates that same pair by 0.448, and
        // polarity is the axis it wins on.
        if token_eq(current, b"no")
            || token_eq(current, b"false")
            || token_eq(current, b"without")
            || token_eq(current, b"neither")
            || token_eq(current, b"nor")
            || token_eq(current, b"none")
            || token_eq(current, b"absent")
            || token_eq(current, b"nil")
            || token_eq(current, b"dry")
        {
            negative = true;
            negation_scope = 6;
        } else if token_eq(current, b"not")
            || token_eq(current, b"hardly")
            || token_eq(current, b"scarcely")
            || token_eq(current, b"lacking")
            || token_eq(current, b"devoid")
            || token_eq(current, b"failed")
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
            // Gated for the same reason as the concept rule, and the measurement that
            // forced it is worth keeping. Left ungated, the ground truth "No. Precipitation
            // in Lagos during the requested UTC hour measured 0.05 mm, below the 0.1 mm
            // threshold." read as both negative and positive, because "measured" sits far
            // enough past "No" that the negation scope has lapsed. The truth collapsed to
            // Unknown, which disabled the polarity check altogether and let a wrong "Yes"
            // answer rise from 0.0000 to 0.4724.
            || (concepts_imply_polarity
                && (token_eq(current, b"fell")
                    || token_eq(current, b"fall")
                    || token_eq(current, b"observed")
                    || token_eq(current, b"recorded")
                    || token_eq(current, b"measured")
                    || token_eq(current, b"detected")
                    || token_eq(current, b"reported")))
            // A weather concept asserted with no negation over it is an affirmative. Asked
            // "Was there a thunderstorm?", the correct "Thunder and lightning were observed
            // over Lagos in that window." read as Unknown and took the missing-binary-answer
            // ceiling of 0.49 from a blend of 0.8029. "Rain did fall in Lagos over that
            // hour." took the same ceiling from 0.9362. Neither contains the word yes, and
            // neither needs to: naming the phenomenon is how a forecast says it happened.
            // Inside a negation scope the existing branch below reads it as a denial, so
            // "no measurable rainfall" is unaffected.
            || (concepts_imply_polarity && is_weather_anchor_signal(current))
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

/// Informativeness weight for a token, so that overlap can be measured on a
/// continuous scale.
///
/// Set F1 over token sets is coarse by construction: across sets of size 2 to 8 it
/// can only take 40 distinct values. That is a large part of why this module emitted
/// 30 distinct scores over 80 third-party benchmark answers where the incumbent
/// champion emitted 75. Weighting each token by a real number makes the same overlap
/// measure continuous, and gives the weight a meaning: a number carries more of a
/// factual claim than an ordinary noun, and a proper noun carries more than a common
/// word.
///
/// Corpus-free by necessity. There is no document collection to derive IDF from
/// inside a `no_std` module with no imports, so token shape stands in for it.
fn token_salience(token: &[u8]) -> f32 {
    if token.iter().any(u8::is_ascii_digit) {
        // A number is the sharpest factual content an answer can carry, and getting
        // one wrong is the most direct way to be wrong.
        return 3.0;
    }
    if token_is_all_uppercase(token) {
        return 2.5;
    }
    if token_is_titlecase(token) {
        return 2.0;
    }
    // Ordinary words: longer carries more, saturating so a single long word cannot
    // dominate a short clause.
    1.0 + (token.len() as f32 / 12.0).min(0.8)
}

/// Salience-weighted F1 between the ground truth and the answer.
///
/// Same shape as `semantic_f1`, which it replaces, but each token contributes its
/// weight rather than a count. Each distinct token is weighted once, so repeating a
/// term cannot inflate either side.
fn salience_weighted_f1(ground_truth: &str, answer: &str) -> Option<f32> {
    let truth_set = semantic_set(ground_truth);
    let answer_set = semantic_set(answer);
    if truth_set.len == 0 {
        return None;
    }
    if answer_set.len == 0 {
        return Some(0.0);
    }

    let mut truth_weight = 0.0f32;
    let mut matched_truth_weight = 0.0f32;
    let mut counted = HashSet::<MAX_SEMANTIC_TOKENS>::new();
    for token in TokenIter::new(ground_truth) {
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        if counted.contains(hash) {
            continue;
        }
        counted.insert(hash);
        let weight = token_salience(token.bytes);
        truth_weight += weight;
        if answer_set.contains(hash) {
            matched_truth_weight += weight;
        }
    }

    let mut answer_weight = 0.0f32;
    let mut matched_answer_weight = 0.0f32;
    let mut counted_answer = HashSet::<MAX_SEMANTIC_TOKENS>::new();
    for token in TokenIter::new(answer) {
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        if counted_answer.contains(hash) {
            continue;
        }
        counted_answer.insert(hash);
        let weight = token_salience(token.bytes);
        answer_weight += weight;
        if truth_set.contains(hash) {
            matched_answer_weight += weight;
        }
    }

    if truth_weight == 0.0 || answer_weight == 0.0 {
        return Some(0.0);
    }
    let recall = matched_truth_weight / truth_weight;
    let precision = matched_answer_weight / answer_weight;
    if precision + recall == 0.0 {
        Some(0.0)
    } else {
        Some((2.0 * precision * recall) / (precision + recall))
    }
}

const MAX_ROLE_PAIRS: usize = 16;
const ROLE_WINDOW: usize = 6;

/// A relation that binds two entities in a fixed order, plus whether this
/// particular wording states it inverted.
///
/// Dimension ids keep antonyms comparable while keeping unrelated comparisons
/// apart: "taller" and "shorter" are the same dimension with opposite sign, so
/// "Everest is taller than K2" and "K2 is shorter than Everest" state one fact, and
/// a scorer that reads only token order would call the second a contradiction. But
/// "longest" and "deepest" are different dimensions, so they are deliberately NOT
/// comparable, which stops this machinery from silently accepting a swapped
/// predicate as a paraphrase.
#[derive(Clone, Copy, PartialEq, Eq)]
struct RoleRelation {
    dimension: u8,
    inverted: bool,
}

const DIM_HEIGHT: u8 = 1;
const DIM_SIZE: u8 = 2;
const DIM_SPEED: u8 = 3;
const DIM_LENGTH: u8 = 4;
const DIM_DEPTH: u8 = 5;
const DIM_AGE: u8 = 6;
const DIM_TEMPERATURE: u8 = 7;
const DIM_AMOUNT: u8 = 8;
const DIM_TIME: u8 = 9;
const DIM_TEACH: u8 = 10;
const DIM_PREY: u8 = 11;
const DIM_CAUSE: u8 = 12;
const DIM_FLOW: u8 = 13;
const DIM_AUTHORSHIP: u8 = 14;
const DIM_CONVERT: u8 = 15;
const DIM_PROVIDE: u8 = 16;
const DIM_SPEAK: u8 = 17;
const DIM_PUMP: u8 = 18;
const DIM_EXCHANGE: u8 = 19;

/// Maps a token to the relation it expresses, if any.
fn role_relation(token: &[u8]) -> Option<RoleRelation> {
    const TABLE: &[(&[u8], u8, bool)] = &[
        (b"taller", DIM_HEIGHT, false),
        (b"tallest", DIM_HEIGHT, false),
        (b"higher", DIM_HEIGHT, false),
        (b"highest", DIM_HEIGHT, false),
        (b"shorter", DIM_HEIGHT, true),
        (b"lower", DIM_HEIGHT, true),
        (b"lowest", DIM_HEIGHT, true),
        (b"larger", DIM_SIZE, false),
        (b"largest", DIM_SIZE, false),
        (b"bigger", DIM_SIZE, false),
        (b"biggest", DIM_SIZE, false),
        (b"smaller", DIM_SIZE, true),
        (b"smallest", DIM_SIZE, true),
        (b"faster", DIM_SPEED, false),
        (b"fastest", DIM_SPEED, false),
        (b"slower", DIM_SPEED, true),
        (b"slowest", DIM_SPEED, true),
        (b"longer", DIM_LENGTH, false),
        (b"longest", DIM_LENGTH, false),
        (b"deeper", DIM_DEPTH, false),
        (b"deepest", DIM_DEPTH, false),
        (b"shallower", DIM_DEPTH, true),
        (b"older", DIM_AGE, false),
        (b"oldest", DIM_AGE, false),
        (b"younger", DIM_AGE, true),
        (b"youngest", DIM_AGE, true),
        (b"hotter", DIM_TEMPERATURE, false),
        (b"warmer", DIM_TEMPERATURE, false),
        (b"colder", DIM_TEMPERATURE, true),
        (b"cooler", DIM_TEMPERATURE, true),
        (b"more", DIM_AMOUNT, false),
        (b"less", DIM_AMOUNT, true),
        (b"fewer", DIM_AMOUNT, true),
        (b"before", DIM_TIME, false),
        (b"predates", DIM_TIME, false),
        (b"earlier", DIM_TIME, false),
        (b"after", DIM_TIME, true),
        (b"later", DIM_TIME, true),
        (b"taught", DIM_TEACH, false),
        (b"teaches", DIM_TEACH, false),
        (b"teach", DIM_TEACH, false),
        (b"preys", DIM_PREY, false),
        (b"prey", DIM_PREY, false),
        (b"preyed", DIM_PREY, false),
        (b"eats", DIM_PREY, false),
        (b"causes", DIM_CAUSE, false),
        (b"cause", DIM_CAUSE, false),
        (b"caused", DIM_CAUSE, false),
        (b"raises", DIM_CAUSE, false),
        (b"flows", DIM_FLOW, false),
        (b"flow", DIM_FLOW, false),
        (b"empties", DIM_FLOW, false),
        (b"receives", DIM_FLOW, true),
        (b"wrote", DIM_AUTHORSHIP, false),
        (b"writes", DIM_AUTHORSHIP, false),
        (b"written", DIM_AUTHORSHIP, false),
        (b"authored", DIM_AUTHORSHIP, false),
        (b"converts", DIM_CONVERT, false),
        (b"convert", DIM_CONVERT, false),
        (b"becomes", DIM_CONVERT, false),
        (b"become", DIM_CONVERT, false),
        (b"provides", DIM_PROVIDE, false),
        (b"provide", DIM_PROVIDE, false),
        (b"speaks", DIM_SPEAK, false),
        (b"speak", DIM_SPEAK, false),
        (b"spoken", DIM_SPEAK, true),
        (b"pumps", DIM_PUMP, false),
        (b"pump", DIM_PUMP, false),
        (b"exchange", DIM_EXCHANGE, false),
        (b"exchanges", DIM_EXCHANGE, false),
    ];
    TABLE.iter().find_map(|(word, dimension, inverted)| {
        token_eq(token, word).then_some(RoleRelation {
            dimension: *dimension,
            inverted: *inverted,
        })
    })
}

/// True when `token` is a form of "to be", which together with a following "by"
/// marks the passive voice.
fn is_copula(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[b"is", b"are", b"was", b"were", b"been", b"being"];
    VALUES.iter().any(|value| token_eq(token, value))
}

/// An ordered binding: this relation holds from `actor` to `target`.
#[derive(Clone, Copy)]
struct RolePair {
    /// Relation key. A table relation uses its dimension id; a genitive uses the
    /// hash of its head noun, so "capital of" and "author of" are distinct
    /// relations without needing either in a word list. Dimension ids are 1..=16
    /// and a semantic hash colliding with one of those is a 16-in-2^64 event.
    relation: u64,
    actor: u64,
    target: u64,
    /// Whether each argument is a named thing rather than a common noun. A conflict
    /// between two named things is a substitution; a difference between two common
    /// nouns is usually a paraphrase, and treating those alike took generated-pair
    /// inversions from 8 of 375 to 14 and pairs below the margin floor from 9 to 23.
    actor_named: bool,
    target_named: bool,
}

/// Extracts ordered role bindings from `text`.
///
/// For each relation token, the nearest content word before it becomes the actor
/// and the nearest content word after it becomes the target, within a bounded
/// window and never across a sentence boundary. Two normalisations make the
/// bindings comparable across wordings:
///
///   voice    "B was taught by A" is passive, marked by a copula before the
///            relation and "by" after it, so its roles are exchanged to (A, B).
///   antonym  a relation flagged inverted, such as "shorter" against "taller",
///            exchanges its roles too, so "K2 is shorter than Everest" yields the
///            same binding as "Everest is taller than K2".
///
/// The result is that a genuine paraphrase produces the binding the ground truth
/// produces, and only a real role exchange differs.
fn role_pairs(text: &str, out: &mut [RolePair; MAX_ROLE_PAIRS]) -> usize {
    let mut tokens: [(u64, usize); MAX_TRACKED_TOKENS] = [(0, 0); MAX_TRACKED_TOKENS];
    let mut named: [bool; MAX_TRACKED_TOKENS] = [false; MAX_TRACKED_TOKENS];
    let mut raw: [Option<RoleRelation>; MAX_TRACKED_TOKENS] = [None; MAX_TRACKED_TOKENS];
    let mut copula: [bool; MAX_TRACKED_TOKENS] = [false; MAX_TRACKED_TOKENS];
    let mut by_marker: [bool; MAX_TRACKED_TOKENS] = [false; MAX_TRACKED_TOKENS];
    let mut genitive: [bool; MAX_TRACKED_TOKENS] = [false; MAX_TRACKED_TOKENS];
    let mut boundary: [bool; MAX_TRACKED_TOKENS] = [false; MAX_TRACKED_TOKENS];
    let mut count = 0usize;
    let mut previous_end = 0usize;

    for token in TokenIter::new(text) {
        if count == MAX_TRACKED_TOKENS {
            break;
        }
        let start = token.end.saturating_sub(token.bytes.len());
        boundary[count] = has_strong_clause_boundary(text, previous_end, start);
        previous_end = token.end;
        raw[count] = role_relation(token.bytes);
        copula[count] = is_copula(token.bytes);
        by_marker[count] = token_eq(token.bytes, b"by");
        genitive[count] = token_eq(token.bytes, b"of") || token_eq(token.bytes, b"in");
        tokens[count] = (semantic_hash(token.bytes).unwrap_or(0), 0);
        named[count] = token_is_entity_like(token.bytes);
        count += 1;
    }

    let mut len = 0usize;
    for index in 0..count {
        let Some(relation) = raw[index] else {
            continue;
        };
        if len == MAX_ROLE_PAIRS {
            break;
        }
        // Nearest content word before, not crossing a sentence boundary.
        let mut actor = 0u64;
        let mut actor_named = false;
        let mut back = index;
        while back > 0 && index - back < ROLE_WINDOW {
            if boundary[back] {
                break;
            }
            back -= 1;
            if tokens[back].0 != 0 && raw[back].is_none() {
                actor = tokens[back].0;
                actor_named = named[back];
                break;
            }
        }
        // Nearest content word after, same constraints.
        let mut target = 0u64;
        let mut target_named = false;
        let mut passive = false;
        let mut forward = index + 1;
        while forward < count && forward - index < ROLE_WINDOW {
            if boundary[forward] {
                break;
            }
            if by_marker[forward] {
                passive = true;
            }
            if tokens[forward].0 != 0 && raw[forward].is_none() {
                target = tokens[forward].0;
                target_named = named[forward];
                break;
            }
            forward += 1;
        }
        if actor == 0 || target == 0 || actor == target {
            continue;
        }
        // Passive voice needs a copula somewhere in the preceding window as well as
        // the "by", or "written by" in an active clause would read as inverted.
        let copula_before = (index.saturating_sub(ROLE_WINDOW)..index).any(|i| copula[i]);
        let exchange = relation.inverted || (passive && copula_before);
        let (actor, target) = if exchange {
            (target, actor)
        } else {
            (actor, target)
        };
        let (actor_named, target_named) = if exchange {
            (target_named, actor_named)
        } else {
            (actor_named, target_named)
        };
        out[len] = RolePair {
            relation: relation.dimension as u64,
            actor,
            target,
            actor_named,
            target_named,
        };
        len += 1;
    }

    // Genitive relations: "X is the capital of Y" and "the capital of Y is X".
    //
    // This is the pattern behind the third-party word-order-swap attack, where
    // "Paris is the capital of France." and "France is the capital of Paris." differ
    // only in argument order and scored identically at 0.8500, because every other
    // signal here reads a token multiset. The head noun is the relation, so no word
    // list is needed and "capital of", "author of" and "source of" are all distinct
    // relations.
    //
    // Both phrasings must yield the same binding or the honest answer would look like
    // the attack. "Paris is the capital of France." finds its actor before the head
    // noun. "The capital of France is Paris." has nothing but an article before it, so
    // the actor is recovered from the complement after the copula that follows the
    // genitive object. Both produce (Paris, capital, France); only a real exchange
    // produces (France, capital, Paris).
    for index in 0..count {
        if len == MAX_ROLE_PAIRS {
            break;
        }
        // A head noun: ordinary content, not itself a relation word.
        if tokens[index].0 == 0 || raw[index].is_some() || copula[index] {
            continue;
        }
        // Followed closely by a genitive connector.
        let mut connector = None;
        for ahead in index + 1..(index + 3).min(count) {
            if boundary[ahead] {
                break;
            }
            if genitive[ahead] {
                connector = Some(ahead);
                break;
            }
        }
        let Some(connector) = connector else {
            continue;
        };
        // Object of the genitive: nearest content word after the connector.
        let mut target = 0u64;
        let mut target_named = false;
        let mut target_at = connector;
        for ahead in connector + 1..count {
            if boundary[ahead] || ahead - connector > ROLE_WINDOW {
                break;
            }
            if tokens[ahead].0 != 0 && raw[ahead].is_none() && !copula[ahead] {
                target = tokens[ahead].0;
                target_named = named[ahead];
                target_at = ahead;
                break;
            }
        }
        if target == 0 {
            continue;
        }
        // Subject before the head noun, skipping articles and copulas.
        let mut actor = 0u64;
        let mut actor_named = false;
        let mut back = index;
        while back > 0 && index - back < ROLE_WINDOW {
            if boundary[back] {
                break;
            }
            back -= 1;
            if tokens[back].0 != 0 && raw[back].is_none() && !copula[back] && !genitive[back] {
                actor = tokens[back].0;
                actor_named = named[back];
                break;
            }
        }
        if actor == 0 {
            // Fronted genitive: recover the subject from the complement, which is the
            // first content word after a copula following the genitive object.
            let mut seen_copula = false;
            for ahead in target_at + 1..count {
                if boundary[ahead] {
                    break;
                }
                if copula[ahead] {
                    seen_copula = true;
                    continue;
                }
                if seen_copula && tokens[ahead].0 != 0 && raw[ahead].is_none() {
                    actor = tokens[ahead].0;
                    actor_named = named[ahead];
                    break;
                }
            }
        }
        if actor == 0 || actor == target || actor == tokens[index].0 {
            continue;
        }
        out[len] = RolePair {
            relation: tokens[index].0,
            actor,
            target,
            actor_named,
            target_named,
        };
        len += 1;
    }
    len
}

/// True when the answer uses the ground truth's own entities but pairs them up
/// differently.
///
/// This is a distinct failure from a reversed binding and neither overlap nor
/// reversal can see it. Against "The eyes provide sight and the ears provide
/// hearing.", the answer "The eyes provide hearing and the ears provide sight."
/// contains every entity the truth does, so overlap is unchanged, and no single pair
/// is reversed either, since (eyes, hearing) is not (sight, eyes). What changed is
/// the pairing across two clauses.
///
/// The test is deliberately strict: the same relation, the same set of actors, the
/// same set of targets, and at least one pairing the truth does not contain. Anything
/// less would fire on an answer that simply discusses different entities. Requiring
/// two bindings on each side matters too, because with one binding matching sets
/// would mean the pairing already matched.
fn role_binding_recombined(ground_truth: &str, answer: &str) -> bool {
    let blank = RolePair {
        relation: 0,
        actor: 0,
        target: 0,
        actor_named: false,
        target_named: false,
    };
    let mut expected = [blank; MAX_ROLE_PAIRS];
    let mut observed = [blank; MAX_ROLE_PAIRS];
    let expected_len = role_pairs(ground_truth, &mut expected);
    let observed_len = role_pairs(answer, &mut observed);
    if expected_len < 2 || observed_len < 2 {
        return false;
    }

    // Consider each relation the two texts share.
    for anchor in 0..expected_len {
        let relation = expected[anchor].relation;
        if expected[..anchor].iter().any(|p| p.relation == relation) {
            continue; // already considered
        }
        let truth_count = expected[..expected_len]
            .iter()
            .filter(|p| p.relation == relation)
            .count();
        let answer_count = observed[..observed_len]
            .iter()
            .filter(|p| p.relation == relation)
            .count();
        if truth_count < 2 || truth_count != answer_count {
            continue;
        }
        // Same actors and same targets, as sets.
        let actors_match = expected[..expected_len]
            .iter()
            .filter(|p| p.relation == relation)
            .all(|t| {
                observed[..observed_len]
                    .iter()
                    .any(|o| o.relation == relation && o.actor == t.actor)
            })
            && observed[..observed_len]
                .iter()
                .filter(|p| p.relation == relation)
                .all(|o| {
                    expected[..expected_len]
                        .iter()
                        .any(|t| t.relation == relation && t.actor == o.actor)
                });
        let targets_match = expected[..expected_len]
            .iter()
            .filter(|p| p.relation == relation)
            .all(|t| {
                observed[..observed_len]
                    .iter()
                    .any(|o| o.relation == relation && o.target == t.target)
            })
            && observed[..observed_len]
                .iter()
                .filter(|p| p.relation == relation)
                .all(|o| {
                    expected[..expected_len]
                        .iter()
                        .any(|t| t.relation == relation && t.target == o.target)
                });
        if !actors_match || !targets_match {
            continue;
        }
        // Same cast on both sides, so any pairing the truth lacks is a recombination.
        let recombined = observed[..observed_len]
            .iter()
            .filter(|p| p.relation == relation)
            .any(|o| {
                !expected[..expected_len]
                    .iter()
                    .any(|t| t.relation == relation && t.actor == o.actor && t.target == o.target)
            });
        if recombined {
            return true;
        }
    }
    false
}

/// True when the answer states a relation the ground truth also states, but with
/// the two entities exchanged.
///
/// This is the one thing a bag-of-tokens signal cannot see. The third-party
/// word-order-swap attack scored exactly 0.8500 for both the honest answer and the
/// swapped one, because the token multiset is identical and every signal in this
/// module was computed over that multiset.
///
/// Deliberately requires the same dimension and the same unordered entity pair, so
/// it fires only on a genuine exchange and stays silent when the answer talks about
/// something else.
fn role_binding_reversed(ground_truth: &str, answer: &str) -> bool {
    let mut expected = [RolePair {
        relation: 0,
        actor: 0,
        target: 0,
        actor_named: false,
        target_named: false,
    }; MAX_ROLE_PAIRS];
    let mut observed = expected;
    let expected_len = role_pairs(ground_truth, &mut expected);
    let observed_len = role_pairs(answer, &mut observed);
    if expected_len == 0 || observed_len == 0 {
        return false;
    }
    for truth_pair in &expected[..expected_len] {
        for answer_pair in &observed[..observed_len] {
            if answer_pair.relation != truth_pair.relation {
                continue;
            }
            // Same binding: the answer agrees, so nothing to report for this pair.
            if answer_pair.actor == truth_pair.actor && answer_pair.target == truth_pair.target {
                return false;
            }
        }
    }
    for truth_pair in &expected[..expected_len] {
        for answer_pair in &observed[..observed_len] {
            if answer_pair.relation == truth_pair.relation
                && answer_pair.actor == truth_pair.target
                && answer_pair.target == truth_pair.actor
            {
                return true;
            }
        }
    }
    false
}

/// Direction a token asserts a quantity is moving: `Some(true)` up, `Some(false)`
/// down, `None` if it says nothing about direction.
fn trend_direction(token: &[u8]) -> Option<bool> {
    const UP: &[&[u8]] = &[
        b"rise",
        b"rises",
        b"rising",
        b"rose",
        b"risen",
        b"increase",
        b"increases",
        b"increasing",
        b"increased",
        b"grow",
        b"grows",
        b"growing",
        b"grew",
        b"gain",
        b"gains",
        b"climb",
        b"climbs",
        b"climbing",
        b"climbed",
        b"strengthen",
        b"strengthens",
        b"improve",
        b"improves",
        b"improved",
        b"appreciate",
        b"upward",
    ];
    const DOWN: &[&[u8]] = &[
        b"fall",
        b"falls",
        b"falling",
        b"fell",
        b"fallen",
        b"decrease",
        b"decreases",
        b"decreasing",
        b"decreased",
        b"drop",
        b"drops",
        b"dropping",
        b"dropped",
        b"decline",
        b"declines",
        b"declining",
        b"declined",
        b"shrink",
        b"shrinks",
        b"weaken",
        b"weakens",
        b"worsen",
        b"worsens",
        b"depreciate",
        b"downward",
    ];
    if UP.iter().any(|word| token_eq(token, word)) {
        return Some(true);
    }
    if DOWN.iter().any(|word| token_eq(token, word)) {
        return Some(false);
    }
    None
}

/// The single direction a text asserts, or `None` when it asserts none or more than
/// one.
///
/// Returning `None` on mixed direction is load-bearing rather than defensive. A
/// ground truth like "Air pressure falls as altitude increases." states both
/// directions about two different quantities, and with no parse to say which belongs
/// to which, any verdict drawn from it would be a guess. Silence is the honest
/// answer there.
///
/// A negation within three tokens flips the direction, so "prices did not rise" reads
/// as down rather than as agreement with a rising ground truth.
fn asserted_trend(text: &str) -> Option<bool> {
    let mut up = false;
    let mut down = false;
    let mut negation_age = usize::MAX;
    for token in TokenIter::new(text) {
        if is_fact_negation(text, token) {
            negation_age = 0;
            continue;
        }
        if let Some(direction) = trend_direction(token.bytes) {
            let flipped = if negation_age < 3 {
                !direction
            } else {
                direction
            };
            if flipped {
                up = true;
            } else {
                down = true;
            }
        }
        negation_age = negation_age.saturating_add(1);
    }
    match (up, down) {
        (true, false) => Some(true),
        (false, true) => Some(false),
        _ => None,
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

/// Separators binding two figures together, so that "5/10" and "5-10" are recognised as
/// different claims about the same pair of numbers.
///
/// The colon is deliberately absent, and its absence is the whole point of this comment.
/// `numeric_set` skips digits adjacent to a colon, because 14:00 is a clock time rather than
/// a quantity, so a colon can never bind two numbers that this module compares. Counting it
/// here anyway meant that any ground truth carrying a timestamp set an operator bit with no
/// number behind it, and every answer that did not restate the timestamp then differed in
/// mask and was charged with a conflicting numeric binding.
///
/// Weather ground truths carry a timestamp as a matter of course, so this fired across the
/// registered surface. Four correct answers in the core corpus were capped at the 0.49
/// ceiling by it: a wind, humidity, visibility and pressure answer, each of which stated the
/// right figure and simply did not repeat "at 14:00 UTC" back.
fn numeric_operator_mask(text: &str) -> u8 {
    let bytes = text.as_bytes();
    let mut mask = 0u8;
    for (index, byte) in bytes.iter().copied().enumerate() {
        if !matches!(byte, b'/' | b'-') {
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

/// Rank modifiers that demote a superlative claim by one or more places.
///
/// Only the demoting forms are listed. "first" and "last" are deliberately
/// absent: both are common discourse words ("first of all", "last week") and
/// including them would fire on answers that never made a ranking claim.
///
/// The plural "seconds" is absent for the same reason in the other direction. As
/// a bare ordinal "second" demotes a claim, but "30 seconds" is a duration, and
/// only the singular form is treated as a rank.
fn is_demoting_rank_modifier(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"second",
        b"third",
        b"fourth",
        b"fifth",
        b"sixth",
        b"seventh",
        b"eighth",
        b"ninth",
        b"tenth",
        b"2nd",
        b"3rd",
        b"4th",
        b"5th",
        b"6th",
        b"7th",
        b"8th",
        b"9th",
        b"10th",
        b"penultimate",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

/// True when the answer asserts a rank the ground truth does not support.
///
/// This is the defect the entity-binding rule could not reach. Inserting one word
/// into an otherwise faithful copy of the truth inverts the claim while *raising*
/// lexical overlap: against the truth "Everest is known as the tallest mountain on
/// Earth.", the wrong answer "Everest is known as the second tallest mountain on
/// Earth." scored 0.839423 while a correctly-bound paraphrase scored 0.804167. It
/// keeps the right entity, so nothing in the anchor path objects, and it shares
/// more wording with the truth than the paraphrase does, so overlap rewards it. 18
/// of 375 generated pairs failed this way.
///
/// The comparison is asymmetric, and that is deliberate. Introducing a modifier
/// the truth lacks is penalised; omitting one the truth has is not. Omission is
/// ordinary paraphrase: against "Curie is the first woman to win a Nobel Prize.",
/// the answer "Curie won a Nobel Prize before any other woman." drops the ordinal
/// and is still correct. Requiring the two sets to match would fail it.
///
/// Matching is per exact token rather than by presence of any modifier, so a truth
/// that says "second" does not license an answer that says "third".
fn introduces_unsupported_rank_modifier(ground_truth: &str, answer: &str) -> bool {
    TokenIter::new(answer)
        .filter(|token| is_demoting_rank_modifier(token.bytes))
        .any(|token| {
            !TokenIter::new(ground_truth).any(|supported| token_eq(supported.bytes, token.bytes))
        })
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

/// True when the answer drops a named context constraint and names a different thing in
/// its place.
///
/// The pre-existing `context_conflict` test required `context_overlap == 0`, meaning the
/// answer had to match NONE of the context constraints. Asked about Lagos, the answer
/// "Yes, Abuja saw measurable precipitation in that hour." keeps the benign constraint
/// "measurable", so overlap was 1, the conflict never fired, support stayed at a vacuous
/// 1.000, and a wrong city scored 0.9454. The same swap on a non-weather question scored
/// 0.3882, because there the city becomes a value anchor rather than a context
/// constraint. Registration 506 was scored entirely on weather fixtures, so the whole
/// entity apparatus was effectively inert on the only surface that counted.
///
/// Both halves must hold, which is what keeps this off correct answers. A terse but
/// right answer drops the constraint without naming anything else and is untouched, and
/// an answer that keeps Lagos while mentioning some further name has no dropped
/// constraint to pair the novel name with.
/// Novel content words in the answer, excluding hedges, stance words and units.
///
/// This is the older, cruder companion to `context_entity_substituted`, and it earns its
/// place by catching the one shape the precise test cannot: a substituted place at the
/// start of a sentence, "Abuja should have rain tomorrow.", where capitalisation carries
/// no information.
///
/// Its defect was never the counting, it was the vocabulary. "About 65 percent." is a
/// correct, terse answer whose only novel token was the hedge "About", so it counted 1,
/// tripped the conflict and scored 0.3038. Filtering the words that are never names
/// leaves that answer with a count of 0 and keeps the shape this test exists for.
/// Approximation adverbs only. This is the narrow list, used where over-filtering
/// silently disables a working test rather than merely widening it: applying the broad
/// `is_never_a_name` to `novel_context_candidate_count` suppressed counts that were
/// firing correctly and cost 0.16 of average margin on the weather corpus.
fn is_approximation_word(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"about",
        b"almost",
        b"approximately",
        b"around",
        b"barely",
        b"broadly",
        b"close",
        b"essentially",
        b"exactly",
        b"nearly",
        b"precisely",
        b"roughly",
        b"slightly",
        b"somewhat",
        b"virtually",
        // Attribution markers. "Rain tomorrow according to the Met Office." is a correct
        // sourced answer, and while "Met" and "Office" are both recognised as source
        // names, "according" itself was not, leaving it as the single novel candidate.
        // The count then hit exactly 1, the conflict fired, and the answer scored 0.4516
        // against a required 0.7. The defect was one missing function word.
        b"according",
        b"citing",
        b"per",
        b"reportedly",
        b"via",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
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
            && !is_approximation_word(token.bytes)
        {
            candidates.insert(hash);
        }
    }
    candidates.len
}

/// True when the answer is a question rather than an answer.
///
/// From a miner's point of view nothing was answered, and until now such a response fell
/// only to the missing-binary-answer ceiling of 0.49, which is where the fixture's
/// `adversarial_question_copy` case sat at 0.4891. That single value blocked the output
/// transform this module needs in order to compete on separation, since amplifying 0.49
/// pushes it to 0.86.
///
/// A second branch was tried and removed: an answer whose content is drawn almost entirely
/// from the question, carrying none of the ground truth's own vocabulary. It read as a
/// restatement test and behaved as a correct-answer test. Asked about 15:00 to 16:00 UTC,
/// the correct "Yes, precipitation occurred in Lagos between 15:00 and 16:00 UTC." draws
/// almost every token from the question, because that is what answering a question about
/// Lagos at that hour looks like, and its contribution, "occurred", was being filtered as a
/// relation word before the check ever saw it. That branch cost 0.7192 of margin on that
/// single pool and 0.4407 on another. The interrogative test alone catches the case this
/// was written for.
fn answer_answers_nothing(answer: &str) -> bool {
    answer.trim_end().ends_with('?')
}

fn context_entity_substituted(
    question: &str,
    ground_truth: &str,
    answer: &str,
    anchors: &FactAnchors,
    response: &HashSet<MAX_SEMANTIC_TOKENS>,
    question_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
    truth_tokens: &HashSet<MAX_SEMANTIC_TOKENS>,
) -> bool {
    // English capitalises the first word of a sentence regardless of what it is, so
    // titlecase alone does not make a token a name. "Roughly a 65 percent chance of
    // measurable rain in that hour." is a correct answer whose leading hedge read as a
    // proper noun, dropped it to the ambiguity ceiling, and cost separation on exactly
    // the case this check was added to help. These are approximation and stance adverbs
    // that open a forecast; none is ever a name. Kept local to this check rather than
    // added to `is_fact_relation_or_filler`, which feeds anchor construction and
    // relation detection across the module.
    let named = |bytes: &[u8]| {
        token_is_entity_like(bytes)
            && !is_fact_relation_or_filler(bytes)
            && !is_temporal_or_unit_anchor(bytes)
            && !is_weather_anchor_signal(bytes)
            && !is_approximation_word(bytes)
    };
    let mut dropped = false;
    for source in [question, ground_truth] {
        for token in TokenIter::new(source) {
            let Some(hash) = semantic_hash(token.bytes) else {
                continue;
            };
            if anchors.context_constraints.contains(hash)
                && named(token.bytes)
                && !response.contains(hash)
            {
                dropped = true;
            }
        }
    }
    if !dropped {
        return false;
    }
    // Forecast providers are not substituted locations. "ECMWF expects rain tomorrow"
    // and "Rain tomorrow according to the Met Office" both name something absent from
    // the question and the truth while answering correctly, and the module already
    // recognises that class through `context_source_name_hashes`. Reusing it rather than
    // re-deriving it is what keeps this check off correct sourced answers; omitting it
    // failed the existing weather-context test on the first run.
    let sources = context_source_name_hashes(question_tokens, truth_tokens, answer);
    // English capitalises the opening word of a sentence whatever it is, so titlecase
    // there is no evidence of a name. Suppressing sentence-initial tokens wholesale was
    // tried and is wrong in the other direction: it cannot tell "Expect showers
    // tomorrow." from "Abuja should have rain tomorrow.", and the second is a genuine
    // substitution that has to be caught. What separates them is what the token IS, not
    // where it sits, so `named` excludes verbs and stance words and the following token
    // decides attribution.
    let bytes = answer.as_bytes();
    let mut previous_end = 0usize;
    let mut at_sentence_start = true;
    let mut candidate: Option<u64> = None;
    for token in TokenIter::new(answer) {
        let start = token.end.saturating_sub(token.bytes.len());
        if previous_end > 0 {
            at_sentence_start = bytes
                .get(previous_end..start)
                .is_some_and(|gap| gap.iter().any(|byte| matches!(byte, b'.' | b'!' | b'?')));
        }
        previous_end = token.end;
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };

        // A name followed by a reporting verb is attributing the forecast, not being
        // forecast about. "ECMWF expects rain tomorrow." names a provider absent from
        // both question and truth, in the subject slot rather than after "according to",
        // so `context_source_name_hashes` does not cover it and it read as a substituted
        // location. Deciding on the FOLLOWING token is what separates "ECMWF expects"
        // from "Abuja saw".
        if let Some(pending) = candidate.take()
            && !is_reporting_verb(token.bytes)
        {
            let _ = pending;
            return true;
        }
        let novel = !question_tokens.contains(hash)
            && !truth_tokens.contains(hash)
            && !sources.contains(hash);
        // Restored after measurement. Dropping this guard and relying on word lists
        // instead moved the weather corpus from +0.4966 to +0.3396 and the ranking pools
        // from +0.3200 to +0.2413, taking inversions from 9 to 12, while every unit test
        // still passed. Sentence-initial titlecase really is uninformative, and the cost
        // is that "Abuja should have rain tomorrow." is invisible here; the counted test
        // below catches that shape instead.
        if at_sentence_start && !token_is_all_uppercase(token.bytes) {
            continue;
        }
        if named(token.bytes) && novel {
            candidate = Some(hash);
        }
    }
    candidate.is_some()
}

/// Verbs that mark the preceding name as the source of a forecast rather than its
/// subject.
fn is_reporting_verb(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"expects",
        b"expect",
        b"reports",
        b"report",
        b"reported",
        b"forecasts",
        b"forecast",
        b"predicts",
        b"predict",
        b"predicted",
        b"says",
        b"said",
        b"indicates",
        b"indicate",
        b"shows",
        b"show",
        b"showed",
        b"estimates",
        b"estimate",
        b"projects",
        b"project",
        b"models",
        b"warns",
        b"warn",
        b"notes",
        b"observes",
        b"anticipates",
        b"suggests",
        b"puts",
        b"gives",
        b"has",
        b"had",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
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

const MAX_BOUND_ENTITIES: usize = 24;

/// True when `token` names the same entity as `other`, allowing the ordinary
/// morphological variation between a place and its people or a noun and its
/// plural.
///
/// `semantic_hash` has no general stemmer, only hand-written synonym groups for
/// weather terms, so "Brazil" and "Brazilians" are unrelated hashes. A check that
/// demanded an exact match would therefore treat the correct answer "Brazilians
/// speak Portuguese" as having dropped Brazil, and penalise it. Prefix matching
/// closes that specific hole without inventing a stemmer.
///
/// The bounds are what keep it from over-matching. Requiring five shared leading
/// bytes and at most five trailing extras admits Brazil/Brazilians and
/// Portugal/Portugal's, while still separating Portugal from Portuguese, which
/// diverge at the seventh byte and are genuinely different entities. That pair is
/// the reason the rule is a prefix test rather than a shared-stem test.
fn entity_tokens_name_same(token: &[u8], other: &[u8]) -> bool {
    if token_eq(token, other) {
        return true;
    }
    let (shorter, longer) = if token.len() <= other.len() {
        (token, other)
    } else {
        (other, token)
    };
    if shorter.len() < 5 || longer.len() - shorter.len() > 5 {
        return false;
    }
    longer[..shorter.len()]
        .iter()
        .zip(shorter)
        .all(|(left, right)| left.eq_ignore_ascii_case(right))
}

/// Collects the entity-like tokens of `text` that could carry a factual binding,
/// skipping the relation words, weather signals, units and digits that are not
/// entities.
fn binding_entity_tokens<'a>(text: &'a str, out: &mut [&'a [u8]; MAX_BOUND_ENTITIES]) -> usize {
    let mut len = 0usize;
    for token in TokenIter::new(text) {
        if len == out.len() {
            break;
        }
        if !token_is_entity_like(token.bytes)
            || is_fact_relation_or_filler(token.bytes)
            || is_weather_anchor_signal(token.bytes)
            || is_temporal_or_unit_anchor(token.bytes)
            || is_context_provenance_or_modal(token.bytes)
            || is_source_attribution_cue(token.bytes)
            || token.bytes.first().is_some_and(u8::is_ascii_digit)
        {
            continue;
        }
        if out[..len]
            .iter()
            .any(|seen| entity_tokens_name_same(seen, token.bytes))
        {
            continue;
        }
        out[len] = token.bytes;
        len += 1;
    }
    len
}

/// Tokens that can serve as evidence the answer named a *different* subject.
///
/// Deliberately stricter than `binding_entity_tokens`, and the asymmetry is the
/// point. This rule returns a hard zero, so both of its halves should be biased
/// against firing: presence of the expected subject is judged generously, and
/// evidence of a substitute subject is judged strictly.
///
/// The strictness that matters is positional. A capital at the start of a
/// sentence says nothing about whether the word is a name, so "That is correct."
/// would otherwise read as naming an entity called That, and it scored zero
/// before this was added. The boundary test is deliberately the strong one, which
/// treats `.` `;` `!` `?` as sentence ends but not `,`: an earlier version used
/// the ordinary clause boundary, which counted the comma in "Yes, K2 is the
/// tallest mountain on Earth." and hid the substituted subject, taking the
/// generated inversions back from 9 to 42. After a comma a capital is still
/// informative. Acronyms and names containing digits are kept regardless of
/// position, since their shape marks them as names on its own.
fn foreign_entity_candidates<'a>(text: &'a str, out: &mut [&'a [u8]; MAX_BOUND_ENTITIES]) -> usize {
    let mut len = 0usize;
    let mut at_sentence_start = true;
    let mut previous_end = 0usize;
    for token in TokenIter::new(text) {
        let start = token.end.saturating_sub(token.bytes.len());
        if has_strong_clause_boundary(text, previous_end, start) {
            at_sentence_start = true;
        }
        previous_end = token.end;
        let positionally_ambiguous = at_sentence_start
            && token_is_titlecase(token.bytes)
            && !token_is_all_uppercase(token.bytes)
            && !token.bytes.iter().any(u8::is_ascii_digit);
        at_sentence_start = false;

        if len == out.len() {
            break;
        }
        if positionally_ambiguous
            || !token_is_entity_like(token.bytes)
            || is_fact_relation_or_filler(token.bytes)
            || is_weather_anchor_signal(token.bytes)
            || is_temporal_or_unit_anchor(token.bytes)
            || is_context_provenance_or_modal(token.bytes)
            || is_source_attribution_cue(token.bytes)
            || token.bytes.first().is_some_and(u8::is_ascii_digit)
        {
            continue;
        }
        if out[..len]
            .iter()
            .any(|seen| entity_tokens_name_same(seen, token.bytes))
        {
            continue;
        }
        out[len] = token.bytes;
        len += 1;
    }
    len
}

/// Detects an answer that swaps out the entity the question is about, in the one
/// situation where nothing else in the scorer is looking.
///
/// `fact_anchors` deliberately drops truth tokens that already appear in the
/// question, because an anchor the question gives away proves nothing about what
/// the answer knows. For most shapes that is correct. It breaks for a question
/// whose ground truth restates it: "Is Everest the tallest mountain on Earth?"
/// answered by "Yes, Everest is the tallest mountain on Earth." leaves every
/// content token in the question, so the anchor set comes out empty,
/// `fact_anchor_assessment` returns `None`, and the score collapses to lexical
/// overlap with no entity check anywhere in the path. An answer that copies the
/// truth and swaps the subject then beats a correctly-bound paraphrase, measured
/// at 0.8625 against 0.3700, and 45 of 45 generated pairs of this shape ranked
/// the wrong answer first.
///
/// The caller must gate this on `fact_anchor_assessment` returning `None`. That
/// is not a detail, it is what keeps the rule surgical. A first attempt applied it
/// whenever the two halves below held and broke seven existing tests, because
/// answers routinely do both halves innocently: "Paris." drops France, and
/// "ECMWF expects rain tomorrow." drops the city while naming a forecast source.
/// Restricting it to the case where the anchor machinery produced nothing at all
/// means it can only add judgement where there was none, never override
/// judgement that already exists.
///
/// Within that gate the condition is still conjunctive, because each half alone
/// is something correct answers do:
///
///   * dropping the bound entity alone is what terse answers do, and "Yes." is
///     not wrong for a yes/no question
///   * naming an unfamiliar entity alone is what answers that add context do,
///     and extra detail is not a contradiction
///
/// Requiring both means the answer talked about a different subject than the one
/// asked about, which is a contradiction rather than a style difference.
fn question_entity_substituted(question: &str, ground_truth: &str, answer: &str) -> bool {
    let mut question_entities = [&b""[..]; MAX_BOUND_ENTITIES];
    let mut truth_entities = [&b""[..]; MAX_BOUND_ENTITIES];
    let mut answer_entities = [&b""[..]; MAX_BOUND_ENTITIES];
    let question_len = binding_entity_tokens(question, &mut question_entities);
    let truth_len = binding_entity_tokens(ground_truth, &mut truth_entities);
    let answer_len = binding_entity_tokens(answer, &mut answer_entities);
    if question_len == 0 || answer_len == 0 {
        return false;
    }

    // The entities the question asks about and the truth affirms. Agreement
    // between the two is what makes them load-bearing: a name the truth
    // introduces on its own is an anchor and already handled elsewhere.
    let mut dropped_bound_entity = false;
    for bound in &question_entities[..question_len] {
        let affirmed_by_truth = truth_entities[..truth_len]
            .iter()
            .any(|candidate| entity_tokens_name_same(candidate, bound));
        if !affirmed_by_truth {
            continue;
        }
        let present_in_answer = answer_entities[..answer_len]
            .iter()
            .any(|candidate| entity_tokens_name_same(candidate, bound));
        if !present_in_answer {
            dropped_bound_entity = true;
            break;
        }
    }
    if !dropped_bound_entity {
        return false;
    }

    let mut substitute_entities = [&b""[..]; MAX_BOUND_ENTITIES];
    let substitute_len = foreign_entity_candidates(answer, &mut substitute_entities);
    substitute_entities[..substitute_len]
        .iter()
        .any(|candidate| {
            let known_to_question = question_entities[..question_len]
                .iter()
                .any(|known| entity_tokens_name_same(known, candidate));
            let known_to_truth = truth_entities[..truth_len]
                .iter()
                .any(|known| entity_tokens_name_same(known, candidate));
            !known_to_question && !known_to_truth
        })
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
    /// A named context constraint was dropped and a different named thing put in its
    /// place. Separated from `ambiguous_or_stuffed` because the two deserve very
    /// different ceilings: this one means "you answered about somewhere else", which is
    /// a definite defect, where ambiguity only means "I cannot tell what you claimed".
    /// Sharing the 0.49 ambiguity ceiling left a wrong-city answer at 0.4534 against a
    /// correct 0.9784, and registration 506 was rejected for exactly this, separation
    /// too narrow rather than ordering wrong.
    context_entity_substituted: bool,
    /// The answer's entity pairing disagrees with the ground truth's, with a novel
    /// entity introduced after a contrast word. Exposed separately from
    /// `ambiguous_or_stuffed` because it means "you said someone else did it"
    /// rather than "I cannot tell what you claimed", and the two deserve different
    /// weight. It rode inside the ambiguity flag before, which gave a reassigned
    /// relation exactly the same ceiling as vague phrasing and made the two
    /// indistinguishable.
    relation_mismatch: bool,
    ambiguous_or_stuffed: bool,
    /// True when neither an entity anchor nor a context constraint was found, so
    /// nothing here constrains which entity the answer bound. `support` can still
    /// be meaningful in that state, because an acronym alone is enough to build
    /// an assessment, which is why this is a separate flag rather than something
    /// a caller can infer from `Option`.
    no_binding_anchors: bool,
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
    // A negation whose target is not yet known. 0 none, 1 would open the post-anchor
    // scope, 2 the plain scope. Resolved on the next token, because what a negation
    // governs is what decides whether it denies the ground truth or merely rejects an
    // alternative candidate.
    let mut pending_negation = 0u8;
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
            // Deferred rather than opened here. A negation scoped to everything that
            // follows reads "Paris (not Berlin) is the capital of France." as a denial:
            // the scope survives past Berlin, reaches the question token "capital", and
            // fires a contradiction that scores a correct answer 0.000000. Both that
            // answer and "Berlin (not Paris) is the capital of France." scored zero, so
            // the pool could not be ordered at all.
            //
            // What a negation governs decides its meaning. "not Berlin", naming an
            // entity absent from both question and ground truth, rejects an
            // alternative, which is what a careful correct answer does. "not Paris",
            // naming the anchor, denies the truth. The next token settles which.
            pending_negation = if last_anchor_age <= 3 { 1 } else { 2 };
            last_anchor_age = last_anchor_age.saturating_add(1);
            last_entity_age = last_entity_age.saturating_add(1);
            continue;
        }
        if is_fact_refutation(token.bytes) {
            if negation_scope > 0 || post_anchor_negation_scope > 0 || pending_negation != 0 {
                negation_scope = 0;
                pending_negation = 0;
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
        if pending_negation != 0 {
            // Entity-like and named by neither the question nor the ground truth: the
            // negation rejects a candidate rather than denying the answer, so no scope
            // opens and nothing downstream reads it as a contradiction.
            let governs_rejected_alternative = hash.is_some_and(|value| {
                token_is_entity_like(token.bytes)
                    && !anchors.values.contains(value)
                    && !question_tokens.contains(value)
                    && !truth_tokens.contains(value)
                    && !is_fact_relation_or_filler(token.bytes)
            });
            if !governs_rejected_alternative {
                if pending_negation == 1 {
                    post_anchor_negation_scope = 6;
                } else {
                    negation_scope = 6;
                }
            }
            pending_negation = 0;
        }
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
    // Not applied to weather questions. A weather ground truth such as "Precipitation in Lagos
    // during the requested UTC hour measured 0.05 mm, below the 0.1 mm threshold." has the
    // syntax of two directed relations and the content of one assertion with an apposition, so a
    // correct answer that states the measurement and refers to the threshold without repeating
    // its value read as a partially answered multi-relation truth and was capped at 0.4524. The
    // rest of the entity and relation machinery has been scoped away from the weather path for
    // the same reason: weather truths carry the question's own figures as context, not as
    // separate claims the answer must each restate.
    let incomplete_predicate_free_multi_answer = !weather_question
        && expected_directed_pairs.len > 1
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

    let entity_substitution = weather_question
        && anchors.context_constraints.len > 0
        && context_entity_substituted(
            question,
            ground_truth,
            answer,
            &anchors,
            &response,
            &question_tokens,
            &truth_tokens,
        );
    // Retired: `context_overlap == 0 && novel_context_candidate_count(..) == 1`. It was a
    // proxy for "the answer talks about something else", and the exact-one count was
    // there to spare paraphrases that introduce several new words. It fired on correct
    // terse answers, "About 65 percent." among them, and missed a swapped city whenever
    // the answer kept any one benign context word. `context_entity_substituted` tests the
    // thing itself: a named or locative-slot constraint dropped and another put in place.
    let context_conflict = weather_question
        && anchors.context_constraints.len > 0
        && ((context_overlap == 0
            && novel_context_candidate_count(&question_tokens, &truth_tokens, answer) == 1)
            || context_entity_substituted(
                question,
                ground_truth,
                answer,
                &anchors,
                &response,
                &question_tokens,
                &truth_tokens,
            ));

    // Kept, against an attempt to remove it. Forgiving the omission of question-supplied
    // context looked right: a miner asked about Lagos need not repeat Lagos. Measured, it
    // took the agreement proxy against the live champion from 0.5190 to 0.4032. The
    // champion rewards completeness, scoring the answers that do carry the location and
    // the hour at 0.998 and everything else at about 0.01, so forgiving omission moves our
    // ranking away from theirs rather than toward it. The reading that led to the change,
    // that the champion ranked a terse answer third, was noise: its ranks three through
    // eight all sit within 0.005 of each other.
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
        context_entity_substituted: entity_substitution,
        relation_mismatch,
        no_binding_anchors: anchors.values.len == 0 && anchors.context_constraints.len == 0,
        ambiguous_or_stuffed: connector_ambiguity
            || relation_mismatch
            || directed_relation_mismatch
            || entity_recombination
            || max_anchor_repeats > 3
            || context_conflict,
    })
}

#[allow(clippy::type_complexity)]
fn numeric_scan(text: &str) -> (HashSet<MAX_NUMERIC_FACTS>, [f32; MAX_NUMERIC_FACTS], usize) {
    let mut set = HashSet::new();
    let mut values = [0.0f32; MAX_NUMERIC_FACTS];
    let mut count = 0usize;
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
            if let Some(slot) = values.get_mut(count) {
                *slot = normalized;
                count += 1;
            }
            set.insert(normalized.to_bits() as u64);
        }
    }
    (set, values, count)
}

fn numeric_set(text: &str) -> HashSet<MAX_NUMERIC_FACTS> {
    numeric_scan(text).0
}

/// True when `candidate` is close enough to `expected` to be the same claim.
///
/// The champion's author describes this as the change that took the weather intent:
/// real forecast traffic is a pile of valid forecasts differing by a degree or two, so a
/// numeric check without tolerance punishes every honest forecast that says 23 where the
/// reference said 24. We had no tolerance at all. Against a ground truth of "29.4 degrees
/// Celsius", the correct "It was about 29 degrees Celsius" shared no exact value, so it
/// scored 0.2866, which is precisely what the wrong "about 12 degrees Celsius" scored.
/// The pair tied on a clamp.
///
/// Five percent of the reference with a floor of half a unit, so 29.4 accepts 29 and
/// rejects 12, and 1012 hPa accepts 1010 and rejects 870.
fn numeric_within_tolerance(expected: f32, candidate: f32) -> bool {
    // Ten percent with a floor of one unit. Five percent with a floor of half a unit was
    // too tight for small integer quantities: a wind of 9 km/h against a reference of 8
    // was charged as a conflicting fact, where the champion ranks that same answer first.
    let tolerance = (expected.abs() * 0.10).max(1.0);
    (expected - candidate).abs() <= tolerance
}

/// Fraction of the truth's figures the answer states within tolerance.
fn numeric_agreement(ground_truth: &str, answer: &str) -> Option<f32> {
    let (_, truth, truth_len) = numeric_scan(ground_truth);
    if truth_len == 0 {
        return None;
    }
    let (_, response, response_len) = numeric_scan(answer);
    if response_len == 0 {
        return Some(0.0);
    }
    let matched = truth[..truth_len]
        .iter()
        .filter(|expected| {
            response[..response_len]
                .iter()
                .any(|candidate| numeric_within_tolerance(**expected, *candidate))
        })
        .count();
    Some(matched as f32 / truth_len as f32)
}

fn numeric_quality(ground_truth: &str, answer: &str) -> Option<f32> {
    if let Some(agreement) = numeric_agreement(ground_truth, answer) {
        return Some(agreement);
    }
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
    // Tolerant, for the same reason as `numeric_agreement`: a forecast a degree off the
    // reference is a plausible forecast, not a conflicting fact.
    let (_, truth, truth_len) = numeric_scan(ground_truth);
    let (_, response, response_len) = numeric_scan(answer);
    truth_len > 0
        && response_len > 0
        && !truth[..truth_len].iter().any(|expected| {
            response[..response_len]
                .iter()
                .any(|candidate| numeric_within_tolerance(*expected, *candidate))
        })
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

pub(crate) fn is_weather_question(question: &str) -> bool {
    let mut weather_word = false;
    let mut forecast_word = false;
    let mut temporal_cue = false;
    // Weather concepts count only as lowercase words, the same guard already
    // applied to "weather" and "forecast" below. Without it a capitalised proper
    // noun that collides with a weather synonym group routes a general-knowledge
    // question down the weather path: "Sun" sits in the CLEAR group, so "Is
    // Mercury the closest planet to the Sun?" satisfied the weather-concept and
    // binary-question clause, picked up context constraints and the
    // missing-probability ceiling, and pinned a correct answer at 0.490000 while
    // a wrong one scored 0.965625.
    let mut lowercase_weather_concept = false;
    for token in TokenIter::new(question) {
        let lowercase_word = !token_is_entity_like(token.bytes);
        lowercase_weather_concept |= lowercase_word
            && core::str::from_utf8(token.bytes)
                .is_ok_and(|value| weather_concept_mask(value) != 0);
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
    let has_weather_concept = lowercase_weather_concept;
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
        // Twice the absolute error, not once. A forecast that says 35% where the truth
        // is 65% is a different forecast, but the gentler curve scored it 0.70 against
        // the correct answer's 1.00, and after averaging across four signals and a 0.30
        // weight that became 0.02 of separation. At this slope 65 against 35 scores
        // 0.40 and 65 against 5 scores 0.00, while an answer rounding 65 to 70 still
        // scores 0.90. Registration 506 was rejected for separation, and a signal this
        // flat on the quantity the question asks about is a direct cause.
        total += answer_probability
            .map(|actual| (1.0 - (2.0 * (expected - actual).abs())).clamp(0.0, 1.0))
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

/// Width of the band that flagged scores are compressed into, just below their
/// ceiling. Larger keeps more resolution among flagged answers; smaller costs the
/// unflagged range less.
const CAP_BAND: f32 = 0.04;

/// Applies a ceiling while preserving order on both sides of it.
///
/// Three shapes were measured here and the first two both failed:
///
///   `score.min(c)`  truncates. Every value above `c` collapses onto exactly `c`,
///                   which is what made this module emit 30 distinct values across
///                   80 scores where the incumbent emitted 75, and what produced a
///                   string of exact ties between correct and wrong answers.
///
///   `score * c`     scales the whole range. Monotone, but it charges every answer
///                   that trips a flag, and these flags have false positives: they
///                   fire on correct answers too. Under truncation a false positive
///                   was free whenever the score already sat below `c`; under
///                   scaling it always costs. Measured, that took generated pairs
///                   below the 0.15 margin floor from 9 of 375 to 56, and turned a
///                   ranking-pool tie into an inversion.
///
///   this            compresses. `[0, c]` maps to `[0, c - CAP_BAND]` and `(c, 1]`
///                   maps to `(c - CAP_BAND, c]`. Continuous, monotone across the
///                   whole domain, never exceeds `c`, and charges an answer already
///                   below the ceiling only the narrow band rather than a
///                   proportional cut.
fn cap_preserving_order(score: f32, ceiling: f32) -> f32 {
    let band = CAP_BAND.min(ceiling * 0.5);
    let knee = ceiling - band;
    if score <= ceiling {
        // Squeeze the unflagged-magnitude range into [0, knee]. At ceiling 0.49 and
        // a 0.04 band this is a 92% factor, against the 49% a proportional cut
        // would apply.
        score * (knee / ceiling)
    } else {
        // Map (ceiling, 1] into (knee, ceiling], so two flagged answers of different
        // quality stay distinguishable instead of both landing on the constant.
        knee + band * ((score - ceiling) / (1.0 - ceiling))
    }
}

const MAX_CLAUSES: usize = 8;

/// True when `token` coordinates two clauses, so that what follows is a separate
/// claim rather than a continuation of the current one.
fn is_clause_coordinator(token: &[u8]) -> bool {
    const VALUES: &[&[u8]] = &[
        b"and",
        b"but",
        b"while",
        b"whereas",
        b"though",
        b"although",
        b"however",
        b"yet",
    ];
    VALUES.iter().any(|value| token_eq(token, value))
}

/// Byte ranges of the clauses in `text`.
///
/// Splits on sentence punctuation, on commas, and before a coordinator, because a
/// mixed answer joins its claims with exactly those. A clause is the unit a
/// contradiction lives in: "Socrates taught Plato, and Plato taught Socrates."
/// contradicts the ground truth in its second clause while its first clause states
/// the truth verbatim.
fn clause_ranges(text: &str, out: &mut [(usize, usize); MAX_CLAUSES]) -> usize {
    let bytes = text.as_bytes();
    let mut len = 0usize;
    let mut start = 0usize;
    let mut previous_end = 0usize;
    for token in TokenIter::new(text) {
        let token_start = token.end.saturating_sub(token.bytes.len());
        let split_before = bytes.get(previous_end..token_start).is_some_and(|gap| {
            gap.iter()
                .any(|byte| matches!(byte, b',' | b';' | b'.' | b'!' | b'?' | b'\n'))
        }) || is_clause_coordinator(token.bytes);
        if split_before && token_start > start {
            if len < MAX_CLAUSES {
                out[len] = (start, previous_end.max(start));
                len += 1;
            }
            start = token_start;
        }
        previous_end = token.end;
    }
    if len < MAX_CLAUSES && previous_end > start {
        out[len] = (start, previous_end);
        len += 1;
    }
    len
}

/// True when the answer asserts a binding that disagrees with one the ground truth
/// states, sharing one argument and differing on the other.
///
/// Distinct from a reversal and from a recombination. Against "Paris is the capital of
/// France.", the clause "Berlin is the capital of France." keeps the relation and the
/// target and substitutes the actor. Nothing is reversed and the cast is not the
/// truth's, so neither earlier check applies.
fn role_binding_conflicts(ground_truth: &str, answer: &str) -> bool {
    let blank = RolePair {
        relation: 0,
        actor: 0,
        target: 0,
        actor_named: false,
        target_named: false,
    };
    let mut expected = [blank; MAX_ROLE_PAIRS];
    let mut observed = [blank; MAX_ROLE_PAIRS];
    let expected_len = role_pairs(ground_truth, &mut expected);
    let observed_len = role_pairs(answer, &mut observed);
    for truth_pair in &expected[..expected_len] {
        for answer_pair in &observed[..observed_len] {
            if answer_pair.relation != truth_pair.relation {
                continue;
            }
            let same_actor = answer_pair.actor == truth_pair.actor;
            let same_target = answer_pair.target == truth_pair.target;
            if same_actor && same_target {
                continue;
            }
            // One argument shared and the other replaced. Both the replaced argument
            // and the truth's original must be named things: swapping Berlin for Paris
            // is a substitution, whereas "mountain" becoming "peak" is a paraphrase,
            // and treating those alike put 14 of 375 generated pairs into inversion.
            if same_actor && answer_pair.target_named && truth_pair.target_named {
                return true;
            }
            if same_target && answer_pair.actor_named && truth_pair.actor_named {
                return true;
            }
        }
    }
    false
}

/// True when any single clause of the answer contradicts the ground truth.
///
/// The whole-answer checks are defeated by a mixed answer, and defeated in two
/// different ways. `role_binding_reversed` returns early the moment it finds any
/// binding that agrees, so a correct first clause vouches for a reversed second one:
/// "Socrates taught Plato, and Plato taught Socrates." scored 0.8500. `asserted_trend`
/// deliberately falls silent when a text asserts both directions, which is right for a
/// ground truth describing two quantities but wrong for an answer that asserts a
/// direction and then its opposite: "Bond prices usually rise, and bond prices usually
/// fall." also scored above its honest counterpart.
///
/// Evaluating clause by clause fixes both, because a contradiction is a property of a
/// claim and each clause is one claim.
fn any_clause_contradicts(ground_truth: &str, answer: &str) -> bool {
    let mut clauses = [(0usize, 0usize); MAX_CLAUSES];
    let count = clause_ranges(answer, &mut clauses);
    if count < 2 {
        // A single clause is already covered by the whole-answer checks, and running
        // them again here would only duplicate their penalty.
        return false;
    }
    let truth_trend = asserted_trend(ground_truth);
    for &(start, end) in &clauses[..count] {
        let Some(clause) = answer.get(start..end) else {
            continue;
        };
        if clause.trim().is_empty() {
            continue;
        }
        if role_binding_reversed(ground_truth, clause)
            || role_binding_conflicts(ground_truth, clause)
        {
            return true;
        }
        if let (Some(expected), Some(actual)) = (truth_trend, asserted_trend(clause))
            && expected != actual
        {
            return true;
        }
    }
    false
}

/// Direction a text asserts qualitatively about likelihood: `Some(true)` for likely,
/// `Some(false)` for unlikely, `None` when it says neither or says both.
///
/// Needed because the probability machinery only reads digits. Against a ground truth
/// of "There is a 20% probability", the correct "Unlikely, around one chance in five."
/// and the wrong "Likely, around four chances in five." both contain no digits at all,
/// so both parsed as having no probability and scored identically. A tie loses a
/// fixture case, and stating a forecast in words rather than figures is ordinary
/// phrasing rather than an edge case.
///
/// A negation within three tokens flips the reading, so "not likely" is unlikely.
fn qualitative_likelihood(text: &str) -> Option<bool> {
    const LIKELY: &[&[u8]] = &[
        b"likely",
        b"probable",
        b"probably",
        b"expected",
        b"anticipated",
        b"forecast",
    ];
    const UNLIKELY: &[&[u8]] = &[b"unlikely", b"improbable", b"doubtful", b"dry", b"none"];
    let mut likely = false;
    let mut unlikely = false;
    let mut negation_age = usize::MAX;
    for token in TokenIter::new(text) {
        if is_fact_negation(text, token) {
            negation_age = 0;
            continue;
        }
        let direction = if LIKELY.iter().any(|w| token_eq(token.bytes, w)) {
            Some(true)
        } else if UNLIKELY.iter().any(|w| token_eq(token.bytes, w)) {
            Some(false)
        } else {
            None
        };
        if let Some(direction) = direction {
            let flipped = if negation_age < 3 {
                !direction
            } else {
                direction
            };
            if flipped {
                likely = true;
            } else {
                unlikely = true;
            }
        }
        negation_age = negation_age.saturating_add(1);
    }
    match (likely, unlikely) {
        (true, false) => Some(true),
        (false, true) => Some(false),
        _ => None,
    }
}

/// Floor for an answer that affirms what the ground truth asserts and contradicts none of
/// it.
pub(crate) const POSITIVE_EVIDENCE_FLOOR: f32 = 0.90;

/// True when the answer is hedged so far that it cannot be wrong.
///
/// The positive-evidence floor reads agreement on claims, and an answer can agree with every
/// claim while asserting nothing. "Weather in Lagos is variable and precipitation is always
/// possible at some point." names the right concept, carries the right polarity, contradicts
/// nothing, and would be equally true of a dry hour. The floor lifted it from 0.4836 to
/// 0.9189 before this check, past a correct answer's paired score.
/// Temperature scale a text names, when it names exactly one.
///
/// A figure means nothing without its unit, and `truth_claims_affirmed` compared figures
/// while ignoring units entirely. Against a ground truth of "29.4 degrees Celsius", the wrong
/// answer "Around 29.4 degrees Fahrenheit." matched the figure, matched the temperature
/// concept, contradicted nothing the check knew how to test, and took the positive floor to
/// 0.9900 while the correct answer sat at 0.0686. Under the output transform that pair
/// inverted by 0.9214, the largest single error in any corpus here.
fn temperature_scale(text: &str) -> Option<u8> {
    let mut scale = None;
    for token in TokenIter::new(text) {
        let found = if token_eq(token.bytes, b"celsius")
            || token_eq(token.bytes, b"centigrade")
            || token.bytes == b"C"
        {
            Some(1u8)
        } else if token_eq(token.bytes, b"fahrenheit") || token.bytes == b"F" {
            Some(2)
        } else if token_eq(token.bytes, b"kelvin") || token.bytes == b"K" {
            Some(3)
        } else {
            None
        };
        if let Some(value) = found {
            if scale.is_some_and(|current| current != value) {
                // Two scales named, as in a correct answer that converts between them.
                return None;
            }
            scale = Some(value);
        }
    }
    scale
}

/// True when the answer states a temperature on a different scale from the ground truth.
fn temperature_scale_conflict(ground_truth: &str, answer: &str) -> bool {
    match (temperature_scale(ground_truth), temperature_scale(answer)) {
        (Some(expected), Some(actual)) => expected != actual,
        _ => false,
    }
}

fn answer_is_unfalsifiable(answer: &str) -> bool {
    const MARKERS: &[&[u8]] = &[
        b"possible",
        b"possibly",
        b"variable",
        b"always",
        b"sometimes",
        b"usually",
        b"often",
        b"occasionally",
        b"may",
        b"might",
        b"could",
        b"unpredictable",
        b"changeable",
    ];
    TokenIter::new(answer).any(|token| MARKERS.iter().any(|word| token_eq(token.bytes, word)))
}

/// Whether the answer affirms the ground truth's claims without contradicting any, and how
/// many distinct claims it affirms.
///
/// Every other rule in this module is a deduction, so a correct answer earns nothing for
/// being correct: it only avoids some of the penalties, and it fails others by construction.
/// A paraphrase does not repeat the truth's wording, so the anchor machinery reads a missing
/// anchor; a terse answer omits the location and the timestamp the question already supplied,
/// so coverage is charged for the omission. Measured on the core discrimination corpus, our
/// correct answers averaged 0.6483 while the live champion places its accepted answers at
/// about 0.998, and on the generated corpus a correct terse answer lands adjacent to the
/// clamp value its wrong counterpart was capped onto. That adjacency is why two attempts at
/// an output transform could not separate them: a monotonic transform cannot pull apart two
/// scores the judgement placed side by side.
///
/// Claims are the things a ground truth actually asserts and a scorer can check: its
/// polarity, its figures within tolerance, its weather concepts, its probability, and the
/// entity binding the anchor machinery already computes. Repeating the truth's phrasing is
/// not one of them.
///
/// Contradicting any claim disqualifies the answer outright. Affirming fewer than two leaves
/// it alone, so a bare "Yes." to a question whose truth asserts a figure and a concept does
/// not reach the floor on polarity alone.
fn truth_claims_affirmed(
    ground_truth: &str,
    answer: &str,
    truth_polarity: Polarity,
    answer_polarity: Polarity,
    truth_probability: Option<f32>,
    answer_probability: Option<f32>,
    support: Option<f32>,
) -> Option<u8> {
    let mut affirmed = 0u8;

    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative) {
        match answer_polarity {
            Polarity::Positive | Polarity::Negative if answer_polarity == truth_polarity => {
                affirmed += 1;
            }
            Polarity::Positive | Polarity::Negative => return None,
            _ => {}
        }
    }

    if let Some(expected) = truth_probability
        && let Some(actual) = answer_probability
    {
        if (expected - actual).abs() > 0.10 {
            return None;
        }
        affirmed += 1;
    }

    // Every figure the answer states must be one the truth supports. Figures the truth
    // states and the answer omits are not held against it, because the question supplies
    // most of them and a miner has no reason to read them back.
    let (_, truth_numbers, truth_len) = numeric_scan(ground_truth);
    let (_, answer_numbers, answer_len) = numeric_scan(answer);
    if answer_len > 0 && truth_len > 0 {
        let mut matched = 0usize;
        for candidate in &answer_numbers[..answer_len] {
            if truth_numbers[..truth_len]
                .iter()
                .any(|expected| numeric_within_tolerance(*expected, *candidate))
            {
                matched += 1;
            } else {
                return None;
            }
        }
        if matched > 0 {
            affirmed += 1;
        }
    }

    if temperature_scale_conflict(ground_truth, answer) {
        return None;
    }

    let truth_concepts = crate::text::weather_concept_mask(ground_truth);
    if truth_concepts != 0 {
        let answer_concepts = crate::text::weather_concept_mask(answer);
        if answer_concepts & truth_concepts != 0 {
            affirmed += 1;
        }
    }

    if support.is_some_and(|value| value >= 0.75) {
        affirmed += 1;
    }

    Some(affirmed)
}

/// Threshold at which a judgement becomes a pass, the width of the ramp through it, and the
/// share of the raw score kept afterwards.
pub(crate) const STEP_THRESHOLD: f32 = 0.55;
pub(crate) const STEP_RAMP_WIDTH: f32 = 0.24;
pub(crate) const STEP_RAW_SHARE: f32 = 0.10;

/// Push the judgement toward a step, then add some of the raw score back.
///
/// Registration 506 was rejected on average separation, 0.1929 against 0.4941, and 518 on the
/// same ground, 0.2030 against 0.4561, both while winning 11 of 12 cases on ordering. Those
/// facts together say the problem is not which answer we prefer but by how much: a smooth
/// score spread across the unit interval cannot average a large separation however well it
/// ranks. The live champion is very nearly binary, about 0.998 for answers it accepts and
/// about 0.01 for the rest, and its author describes the construction directly, a hard step
/// for separation with a little of the raw score added back.
///
/// The retained share is not a detail. It keeps ordering alive inside each band, and the live
/// agreement gate correlates our ranking of real miner answers against the holder's, where a
/// band of identical values is a block of ties that correlates with nothing. Being monotonic
/// the whole transform reorders nothing, so it cannot turn a win into a loss and cannot move
/// the agreement measurement at all.
///
/// This is the third attempt. The first two were reverted, and the reasons are why it works
/// now. A hard step took generated pairs below the 0.15 margin floor from 9 to 135 of 375,
/// because two answers on the same side of the threshold collapse together; a finite ramp
/// halved that. Amplification then exposed leniency rather than fixing it: an answer that
/// merely echoed the question rose to 0.8637 against a recorded ceiling of 0.49, and a 51%
/// answer against a 90% truth to 0.9919 against 0.61. Both are now judged at the raw level,
/// 0.1745 and 0.2651. The second attempt failed differently, on correct answers rather than
/// wrong ones: a terse correct answer sat adjacent to the clamp value its wrong counterpart
/// had been capped onto, and nothing monotonic can separate two adjacent scores. Freeing
/// those answers, chiefly by no longer charging a numeric binding conflict for an omitted
/// timestamp, moved the core corpus from +0.4025 to +0.6098 and is what made this viable.
///
/// Threshold and width were swept across four corpora against the recorded fixture ceilings.
/// The threshold sits above every ceiling constant this module applies, so an answer we
/// deliberately capped lands in the low band rather than just above the boundary.
///
/// Applied only to weather questions, which is the registered intent. Left unscoped it took
/// the count of generated pairs below the 0.15 floor from 9 to 96 of 375, all of it in
/// identity_binary and negation_polarity, which are general-knowledge templates: a terse
/// correct answer and a capped wrong one land adjacent there, and nothing monotonic separates
/// adjacent scores. Every improvement that created the headroom this transform needs was
/// itself scoped to the weather path, so the amplification is scoped to match. On other
/// questions the module returns its judgement unchanged.
///
/// It amplifies our mistakes by the same factor as our judgements, so the inversion counts in
/// the frozen bar matter more under this transform, not less.
pub(crate) fn separation_step(score: f32) -> f32 {
    let ramp = (0.5 + ((score - STEP_THRESHOLD) / STEP_RAMP_WIDTH)).clamp(0.0, 1.0);
    (((1.0 - STEP_RAW_SHARE) * ramp) + (STEP_RAW_SHARE * score)).clamp(0.0, 1.0)
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
    // An answer byte-identical to the ground truth IS the ground truth, so it is
    // correct by definition and must score at the top. This has to be decided before
    // any of the rejection paths below, and that ordering is the whole point.
    //
    // Eight `zero(...)` returns used to run ahead of the exact-match check further
    // down: input limits, malformed JSON, a time in the answer absent from the
    // question, keyword stuffing, contradictory truth polarity, contradictory
    // probability. Every one of them fires identically when the answer and the ground
    // truth are the same text, because they are checks on the text itself. So any
    // ground truth that tripped one scored 0.0000 against itself.
    //
    // Telegraph's node enforces a self-match floor of 0.75 and rejected registration
    // 496 on exactly this, saying a ground-truth answer scored against itself gave
    // 0.0000 on at least one fixture. The same failure had been visible for two days
    // in the third-party harness as "non-text ground truth self-matches score=0.0000",
    // scoring `score(weird, weird, weird)` where weird mixes emoji, CJK, RTL and
    // invalid UTF-8 bytes. I recorded it as pre-existing and moved on, which was the
    // wrong call: a judge that cannot recognise the correct answer when handed it
    // verbatim has no business ranking anything.
    //
    // Emptiness is still checked first, because an empty answer must score zero even
    // when the ground truth is also empty.
    if issues & (ISSUE_EMPTY_ANSWER | ISSUE_EMPTY_GROUND_TRUTH) == 0
        && ground_truth_raw.trim() == miner_answer_raw.trim()
    {
        // Issues are still reported, so a defective ground truth is still visible to
        // anything reading the flags. Only the score changes, because scoring the
        // answer and auditing the fixture are different jobs and this module is asked
        // to do the first.
        return Evaluation { score: 1.0, issues };
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

    let weather_question = is_weather_question(question);
    let truth_polarity = explicit_polarity(ground_truth, weather_question);
    let explicit_answer_polarity = explicit_polarity(answer, weather_question);
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

    let fact_assessment = fact_anchor_assessment(question, ground_truth, answer, weather_question);
    if fact_assessment.is_some_and(|assessment| assessment.contradicted) {
        return zero(ISSUE_CONTRADICTORY_FACT_ANCHOR);
    }
    // Gated on there being no anchor that constrains entity binding, which is
    // exactly the shape this catches: a ground truth that restates its question
    // leaves nothing to anchor on. Ungated, the rule overrides judgement that
    // already exists and misreads terse answers and source citations as
    // contradictions, which broke seven tests.
    //
    // The gate deliberately asks about anchors rather than about the assessment
    // being `None`. An earlier version tested `is_none()` and silently missed 9
    // of the 45 generated cases, because a long ground truth yields an acronym
    // candidate, an acronym alone is enough to return `Some`, and the assessment
    // then existed while still saying nothing about which entity was bound.
    let unanchored_binding = fact_assessment.is_none_or(|assessment| assessment.no_binding_anchors);
    if unanchored_binding && question_entity_substituted(question, ground_truth, answer) {
        return zero(ISSUE_CONTRADICTORY_FACT_ANCHOR);
    }

    if matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && matches!(answer_polarity, Polarity::Positive | Polarity::Negative)
        && truth_polarity != answer_polarity
        && (fact_assessment.is_none() || is_binary_question(question))
    {
        return zero(ISSUE_POLARITY_MISMATCH);
    }

    let semantic_quality = salience_weighted_f1(ground_truth, answer);
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
    let blended = score;
    // Defects scale the score into a band rather than truncating it to the band's
    // ceiling. `score.min(c)` collapses every value above `c` onto exactly `c`;
    // `score *= c` maps [0, 1] onto [0, c] monotonically. Both guarantee a flagged
    // answer never exceeds `c`, but only the second preserves ordering among
    // flagged answers, and ordering is the entire scored property.
    //
    // Measured against a third-party benchmark before this change, this module
    // produced 30 distinct values across 80 scores where the incumbent champion
    // produced 75. 0.3000 appeared eighteen times and 0.4900 six times, both clamp
    // constants rather than judgements. That one property accounts for a string of
    // separately-recorded failures: a correct and a wrong answer tying at exactly
    // 0.490000, three misspelling pairs tying at 0.000000, an argument-order swap
    // tying at 0.8500, and five rule fixes moving Telegraph's per-case count by
    // zero because they only shuffled answers between the same few buckets.
    //
    // Discounts compose by multiplication, so two defects compound rather than the
    // tighter ceiling simply winning. Deliberately no hard zero here: registration
    // 98 lost three cases to two rules that zeroed instead of capped, and a zero can
    // only ever remove a win that a discount might have kept.
    if fact_assessment.is_some_and(|assessment| assessment.ambiguous_or_stuffed) {
        issues |= ISSUE_AMBIGUOUS_FACT_ANCHORS;
        score = cap_preserving_order(score, 0.49);
    }
    // A definite substitution takes a far tighter ceiling than ambiguity. The additive
    // composition floors a wrong answer at 0.45 before any cap, because `factual` reads
    // polarity, probability, concepts and numbers but never entities, and `concision` is
    // 1.0 for every answer under 240 bytes. So the ceiling is what actually separates
    // here, and 0.49 is not a separation. Registration 506 lost on average margin,
    // 0.1929 against the champion's 0.4941, while winning 11 of 12 on ordering: the
    // ceilings were tuned when per-case wins were the metric, and under a separation
    // rubric a confident defect has to land low rather than merely below its pair.
    if fact_assessment.is_some_and(|assessment| assessment.context_entity_substituted) {
        issues |= ISSUE_CONTEXT_ENTITY_SUBSTITUTED;
        score = cap_preserving_order(score, 0.15);
    }
    // A reassigned relation takes a tighter ceiling than vague phrasing, and takes
    // it as a compression rather than a zero.
    //
    // Both readings were measured. Against the truth "Hamlet was written by
    // Shakespeare.", the vague-but-correct "It is Shakespeare that is associated
    // with Hamlet." and the wrong "Shakespeare and Marlowe were contemporaries, but
    // Hamlet was written by Marlowe." both trip ambiguity, so a single shared
    // ceiling cannot order them; pre-ceiling the wrong one actually scored higher,
    // 0.795000 against 0.781250, which truncation hid as a tie. Only
    // relation_mismatch separates them.
    //
    // Registration 98 made this a hard zero and lost three of Telegraph's cases,
    // because a zero can only ever remove a win that a cap might have kept. A
    // second compression composes with the first instead: the wrong answer lands
    // near 0.30 while the correct one stays near 0.49, so the pool orders without
    // anything being driven to zero.
    if fact_assessment.is_some_and(|assessment| assessment.relation_mismatch) {
        score = cap_preserving_order(score, 0.30);
    }
    if is_binary_question(question)
        && matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
        && answer_polarity == Polarity::Unknown
    {
        issues |= ISSUE_MISSING_BINARY_ANSWER;
        score = cap_preserving_order(score, 0.49);
    }
    if numeric_quality(ground_truth, answer) == Some(0.0) {
        let ceiling = if has_conflicting_numeric_facts(ground_truth, answer) {
            0.30
        } else {
            0.49
        };
        score = cap_preserving_order(score, ceiling);
    }
    if numeric_binding_conflict(ground_truth, answer) {
        score = cap_preserving_order(score, 0.49);
    }
    // Clamped to the same ceiling as has_conflicting_numeric_facts, because a rank
    // the truth does not support is the same kind of factual conflict as a number
    // the truth does not support. A clamp rather than a zero: the check reads one
    // inserted word, and an answer that adds a genuine aside such as "K2 is the
    // second tallest" would trip it, so the penalty has to be severe enough to
    // rank the claim below a faithful paraphrase without being fatal.
    if introduces_unsupported_rank_modifier(ground_truth, answer) {
        issues |= ISSUE_RANK_MODIFIER_CONFLICT;
        score = cap_preserving_order(score, 0.30);
    }
    // A reversed role binding is the strongest factual contradiction this module can
    // detect, so it takes the tightest ceiling, and takes it as a compression rather
    // than a zero for the reason registration 98 established: a zero can only remove
    // a win that a cap might have kept.
    //
    // This is the only signal here that is not computed over a token multiset, which
    // is why it is the only one that can see an argument exchange at all. The
    // third-party word-order-swap attack scored exactly 0.8500 for both the honest
    // and the swapped answer, because the multiset is unchanged.
    // Opposite asserted direction, where there are no roles to exchange. The
    // third-party direction-flip attack turns "Bond prices usually rise." into "Bond
    // prices usually fall.": same subject, one word apart, and the honest answer
    // "They tend to increase." shares almost no vocabulary with either. Overlap
    // signals therefore rank the attack above the honest answer, 0.4089 against
    // 0.3000, and no role binding exists to notice.
    if let (Some(truth_trend), Some(answer_trend)) =
        (asserted_trend(ground_truth), asserted_trend(answer))
        && truth_trend != answer_trend
    {
        issues |= ISSUE_TREND_CONTRADICTION;
        score = cap_preserving_order(score, 0.30);
    }
    // A contradiction inside one clause of a multi-clause answer, which the
    // whole-answer checks above cannot see because a correct clause masks it.
    // An answer that answers nothing takes a ceiling far below a partial answer's.
    if answer_answers_nothing(answer) {
        issues |= ISSUE_ANSWERS_NOTHING;
        score = cap_preserving_order(score, 0.20);
    }
    // A figure on the wrong scale is the wrong figure.
    if temperature_scale_conflict(ground_truth, answer) {
        issues |= ISSUE_UNIT_SCALE_CONFLICT;
        score = cap_preserving_order(score, 0.30);
    }
    // An answer that cannot be wrong should not outrank one that can. The evasive
    // "Weather in Lagos is variable and precipitation is always possible at some point."
    // was already denied the positive floor, but its own blend saturated the output
    // transform anyway and it scored 0.9919 against a correct answer's 0.9923.
    if answer_is_unfalsifiable(answer)
        && matches!(truth_polarity, Polarity::Positive | Polarity::Negative)
    {
        issues |= ISSUE_UNFALSIFIABLE_ANSWER;
        score = cap_preserving_order(score, 0.49);
    }
    // A probability materially different from the ground truth's is a different forecast,
    // and until now it was only ever averaged into `factual` alongside three other
    // signals at a combined weight of 0.30, so a 39 point error moved the total by a few
    // hundredths. Against "Rain is expected with 90% probability.", the answer "Rain is
    // expected with 51% probability." scored 0.5807 where a correct paraphrase of the same
    // truth scores 0.8221. That gap is too small to survive amplification: the output
    // transform this module needs in order to compete on separation was measured and
    // reverted precisely because wrong answers sit at 0.5 to 0.6 while right ones sit at
    // 0.85, leaving no room to push the two apart.
    //
    // A quarter of the scale is the threshold. Twenty five points is past rounding, past
    // the difference between two honest forecasts, and on the far side of even odds from
    // most references.
    if let (Some(expected), Some(actual)) = (truth_probability, answer_probability)
        && (expected - actual).abs() > 0.25
    {
        issues |= ISSUE_PROBABILITY_DISAGREEMENT;
        score = cap_preserving_order(score, 0.30);
    }
    // A qualitative forecast that contradicts the ground truth's figure, applied only
    // when the answer states no figure of its own so the numeric agreement term above
    // is not doubled up on.
    if let (Some(expected), None, Some(stated)) = (
        truth_probability,
        answer_probability,
        qualitative_likelihood(answer),
    ) && (expected >= 0.5) != stated
    {
        issues |= ISSUE_LIKELIHOOD_CONTRADICTION;
        score = cap_preserving_order(score, 0.30);
    }
    // An answer whose asserted span is wider than the window the question named. Asked
    // about one hour, "at some point in the 24 hours around it" would be true of many
    // dry hours and does not answer the question; it scored 0.9145 against a correct
    // 0.9480 before this check. Requires a margin of more than double, so an answer
    // rounding a 55 minute window to "about an hour" is untouched.
    if let (Some(window), Some(asserted)) = (
        crate::text::question_window_minutes(question),
        crate::text::asserted_duration_minutes(answer),
    ) && asserted > window.saturating_mul(2)
    {
        issues |= ISSUE_SPAN_WIDER_THAN_WINDOW;
        score = cap_preserving_order(score, 0.30);
    }
    // A calendar year supported by neither the question nor the ground truth.
    if crate::text::asserts_unsupported_year(question, ground_truth, answer) {
        issues |= ISSUE_UNSUPPORTED_YEAR;
        score = cap_preserving_order(score, 0.40);
    }
    // An answer that designates the requested window's closing hour as its own.
    if crate::text::answer_binds_window_end_only(question, answer) {
        issues |= ISSUE_WINDOW_END_ONLY;
        score = cap_preserving_order(score, 0.30);
    }
    if any_clause_contradicts(ground_truth, answer) {
        issues |= ISSUE_CLAUSE_CONTRADICTION;
        score = cap_preserving_order(score, 0.40);
    }
    if role_binding_recombined(ground_truth, answer) {
        issues |= ISSUE_ROLE_BINDING_RECOMBINED;
        score = cap_preserving_order(score, 0.40);
    }
    if role_binding_reversed(ground_truth, answer) {
        issues |= ISSUE_ROLE_BINDING_REVERSED;
        // 0.40 rather than something tighter, and the reason is a frozen gate rather
        // than taste. At 0.20 the reversed-relation candidate in the ranking pools
        // fell from 0.478235 to 0.173912, below the unrelated candidate at 0.337500,
        // which broke the pool's graded expectation that an on-topic reversal ranks
        // above an off-topic answer and took pairwise accuracy from 100/106 to
        // 99/106, under the 0.9400 floor.
        //
        // The tempting fix was to re-grade that pool. That would be tuning the
        // evaluator to fit the code, which is the failure this whole split exists to
        // prevent, so the ceiling moved instead. Whether a confidently reversed claim
        // really should outrank an irrelevant one is a genuine question about grading
        // and belongs to whoever authors the blind corpus, not to the author of the
        // rule being graded.
        score = cap_preserving_order(score, 0.40);
    }
    if let Some(expected_probability) = truth_probability {
        // Already continuous in the data when a probability is present, so the
        // agreement term multiplies; a missing probability takes the same discount
        // as the other defects.
        let ceiling = match answer_probability {
            Some(actual_probability) => {
                (1.0 - (expected_probability - actual_probability).abs()).clamp(0.0, 1.0)
            }
            None => 0.49,
        };
        score = cap_preserving_order(score, ceiling);
    }
    // Scoped to weather questions, which is the registered surface and the only place the
    // claim types below are meaningful. Unscoped it lifted a WRONG answer to the floor on
    // "Which mountain is the highest above sea level?", inverting a pair that had been
    // ordered correctly, because nothing objected to that answer and it affirmed two claims.
    // Any mechanism that raises correct answers also raises the wrong ones we fail to
    // detect, which is the same reason two output transforms were reverted.
    //
    // The positive path. Deliberately sets no issue bit: `issues` records defects, and
    // several tests assert it is exactly zero for a correct answer. Applied only when no
    // ceiling fired, which is what keeps it off
    // answers the rules have already judged: every ceiling routes through
    // `cap_preserving_order`, which lowers the value whenever it applies, so an untouched
    // score is proof that nothing objected.
    if weather_question
        && score >= blended
        && !answer_is_unfalsifiable(answer)
        && let Some(affirmed) = truth_claims_affirmed(
            ground_truth,
            answer,
            truth_polarity,
            answer_polarity,
            truth_probability,
            answer_probability,
            fact_assessment.map(|assessment| assessment.support),
        )
        && affirmed >= 2
    {
        score = score.max(POSITIVE_EVIDENCE_FLOOR);
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

    #[test]
    fn an_inserted_ordinal_ranks_below_a_faithful_paraphrase() {
        const Q: &str = "What is Everest known for?";
        const T: &str = "Everest is known as the tallest mountain on Earth.";

        // Before the fix the demoted claim scored 0.839423 against the
        // paraphrase's 0.804167, because inserting one word raises lexical
        // overlap with the truth while inverting the meaning.
        let demoted = evaluate(
            Q,
            T,
            "Everest is known as the second tallest mountain on Earth.",
        );
        let faithful = evaluate(
            Q,
            T,
            "Everest holds the record: the tallest mountain on Earth.",
        );
        assert_ne!(
            demoted.issues & ISSUE_RANK_MODIFIER_CONFLICT,
            0,
            "{demoted:?}"
        );
        assert!(
            faithful.score > demoted.score,
            "faithful={faithful:?} demoted={demoted:?}"
        );
    }

    #[test]
    fn an_omitted_ordinal_is_ordinary_paraphrase() {
        // Asymmetry check. Dropping an ordinal the truth carries is normal
        // rewording, so only introduction is penalised.
        let evaluation = evaluate(
            "What is Curie known for?",
            "Curie is the first woman to win a Nobel Prize.",
            "Curie won a Nobel Prize before any other woman.",
        );
        assert_eq!(
            evaluation.issues & ISSUE_RANK_MODIFIER_CONFLICT,
            0,
            "{evaluation:?}"
        );
    }

    #[test]
    fn a_supported_ordinal_is_allowed_and_a_different_one_is_not() {
        const Q: &str = "What is K2 known for?";
        const T: &str = "K2 is known as the second tallest mountain on Earth.";

        let supported = evaluate(Q, T, "K2 is the second tallest peak on Earth.");
        assert_eq!(
            supported.issues & ISSUE_RANK_MODIFIER_CONFLICT,
            0,
            "{supported:?}"
        );

        // Matching is per exact token, so a truth that says "second" does not
        // license an answer that says "third".
        let mismatched = evaluate(Q, T, "K2 is the third tallest peak on Earth.");
        assert_ne!(
            mismatched.issues & ISSUE_RANK_MODIFIER_CONFLICT,
            0,
            "{mismatched:?}"
        );
    }

    #[test]
    fn a_duration_in_seconds_is_not_a_rank() {
        assert!(is_demoting_rank_modifier(b"second"));
        assert!(!is_demoting_rank_modifier(b"seconds"));
        // Common discourse words are excluded, or they would fire on answers that
        // never made a ranking claim.
        assert!(!is_demoting_rank_modifier(b"first"));
        assert!(!is_demoting_rank_modifier(b"last"));
    }

    #[test]
    fn a_capitalised_weather_synonym_does_not_make_a_question_meteorological() {
        // "Sun" sits in the CLEAR synonym group, so this binary general-knowledge
        // question was routed down the weather path, picked up the
        // missing-probability ceiling, and pinned the correct answer at 0.490000
        // while the swapped-subject answer scored 0.965625.
        assert!(!is_weather_question(
            "Is Mercury the closest planet to the Sun?"
        ));

        // Residual, and asserted so it is documented rather than assumed away: a
        // *lowercase* weather word used non-meteorologically still routes a binary
        // question down the weather path. Closing it means dropping the
        // weather-concept-plus-binary clause, which changes how a genuine weather
        // question with no temporal cue classifies, so it is recorded as a known
        // limitation instead of guessed at.
        assert!(is_weather_question("Is ice less dense than water?"));

        // Genuine weather questions must still classify as weather.
        assert!(is_weather_question(
            "Will measurable precipitation > 0.1 mm occur in Lagos from 15:00 to 16:00 UTC?"
        ));
        assert!(is_weather_question("What is the weather in Lagos?"));
        assert!(is_weather_question("Is it sunny in Lagos now?"));
    }

    #[test]
    fn a_restated_question_still_penalises_a_swapped_subject() {
        const Q: &str = "Is Everest the tallest mountain on Earth?";
        const T: &str = "Yes, Everest is the tallest mountain on Earth.";

        // The defect this closes. Before the fix the swapped subject scored
        // 0.862500 by copying the truth's wording, while the correctly-bound
        // paraphrase scored 0.370000, so the pool ranked backwards.
        let swapped = evaluate(Q, T, "Yes, K2 is the tallest mountain on Earth.");
        assert_eq!(swapped.score, 0.0, "{swapped:?}");
        assert_ne!(swapped.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);

        let bound = evaluate(Q, T, "Everest is indeed Earth's highest peak.");
        assert!(bound.score > swapped.score, "{bound:?}");
        assert_eq!(bound.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR, 0);
    }

    #[test]
    fn dropping_the_subject_without_naming_another_is_not_a_substitution() {
        const Q: &str = "Is Everest the tallest mountain on Earth?";
        const T: &str = "Yes, Everest is the tallest mountain on Earth.";

        // A terse answer omits the subject and is still correct. Only the
        // conjunction of omission and a foreign subject is a contradiction, so
        // each half alone has to stay clean.
        for answer in ["Yes.", "Yes, it is.", "That is correct."] {
            let evaluation = evaluate(Q, T, answer);
            assert_eq!(
                evaluation.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR,
                0,
                "{answer:?}: {evaluation:?}"
            );
        }
    }

    #[test]
    fn a_named_source_is_not_a_swapped_subject() {
        // Attributing a forecast to its source names an entity that appears in
        // neither question nor truth, while omitting the city. That is a
        // citation, not a contradiction, and an earlier ungated version of this
        // rule scored it zero.
        let evaluation = evaluate(
            "Will measurable precipitation > 0.1 mm occur in Lagos from 15:00 to 16:00 UTC?",
            "Yes. Measurable precipitation occurred in Lagos during the requested UTC hour.",
            "ECMWF expects rain during that hour.",
        );
        assert_eq!(
            evaluation.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR,
            0,
            "{evaluation:?}"
        );
    }

    #[test]
    fn entity_matching_tolerates_demonyms_without_conflating_neighbours() {
        // "Brazilians" has to count as naming Brazil, or the correct answer
        // "Brazilians speak Portuguese." reads as having dropped the subject.
        // There is no general stemmer in semantic_hash, so this is a bounded
        // prefix rule.
        assert!(entity_tokens_name_same(b"Brazil", b"Brazilians"));
        assert!(entity_tokens_name_same(b"Portugal", b"Portugal's"));
        assert!(entity_tokens_name_same(b"everest", b"Everest"));

        // Portugal and Portuguese diverge at the seventh byte and are different
        // entities. Conflating them would silently forgive the country swap in
        // the shared_token_distractor pool, which is the case the prefix bound
        // exists to preserve.
        assert!(!entity_tokens_name_same(b"Portugal", b"Portuguese"));
        assert!(!entity_tokens_name_same(b"Everest", b"K2"));
        assert!(!entity_tokens_name_same(b"Spain", b"Spanish"));
        // Below the five-byte floor only exact matches count, so a short name
        // cannot prefix-match a longer unrelated one.
        assert!(!entity_tokens_name_same(b"Nile", b"Niles"));
    }

    #[test]
    fn a_subject_swap_is_only_judged_where_no_anchor_exists() {
        // With an anchor available the existing assessment owns the verdict.
        // "Paris." drops France and names an entity absent from the question, so
        // an ungated rule scored it zero; the anchor path scores it properly.
        let evaluation = evaluate(
            "What is the capital of France?",
            "Paris is the capital of France.",
            "Paris.",
        );
        assert_eq!(
            evaluation.issues & ISSUE_CONTRADICTORY_FACT_ANCHOR,
            0,
            "{evaluation:?}"
        );
        assert!(evaluation.score > 0.0, "{evaluation:?}");
    }

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
    fn an_exact_self_match_scores_top() {
        // This test previously asserted the opposite, that an exact self-match scores
        // 0.0 whenever the ground truth is itself defective, and was named
        // exact_matches_do_not_bypass_safety_gates. The intent was sound: do not award
        // full marks to a fixture that answers a different window than it asks about,
        // repeats one word forty times, or contradicts its own probability.
        //
        // Telegraph's node overrides that intent. It enforces a self-match floor of
        // 0.75 and rejected registration 496 with "your scorer failed to recognise a
        // known-correct answer. Scoring a ground-truth answer against itself gave
        // 0.0000 on at least one fixture case." The third-party harness had been
        // reporting the same failure for two days as "non-text ground truth
        // self-matches score=0.0000".
        //
        // The node is right for a reason worth stating: scoring an answer and auditing
        // a fixture are different jobs, and this module is asked to do the first.
        // Penalising the miner who reproduced the ground truth verbatim punishes the
        // wrong party for the fixture's defect.
        //
        // One honest limit. The short-circuit returns before the time-window, stuffing,
        // polarity and probability checks run, so their flags are NOT set on a
        // self-match: those checks are skipped rather than overridden. Flags raised
        // before it, such as malformed JSON, are still reported. Preserving the later
        // flags would mean threading a self-match condition through six separate
        // rejection paths in the most sensitive function here, for diagnostics nothing
        // currently consumes, so it is deliberately not done.
        for (question, text) in [
            (
                "Did rain occur from 15:00 to 16:00 UTC?",
                "Rain occurred from 17:00 to 18:00 UTC.",
            ),
            (
                "What happened?",
                "rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain rain",
            ),
            ("Will it rain?", "Rain is likely with a 20% probability."),
        ] {
            let evaluation = evaluate(question, text, text);
            assert_eq!(evaluation.score, 1.0, "{text:?}: {evaluation:?}");
        }

        // A flag raised before the short-circuit is still reported alongside the 1.0.
        let malformed = "{\"content\": unquoted}";
        let malformed_evaluation = evaluate("q", malformed, malformed);
        assert_eq!(malformed_evaluation.score, 1.0, "{malformed_evaluation:?}");
        assert_ne!(malformed_evaluation.issues & ISSUE_MALFORMED_JSON, 0);

        // The gates still bite for an answer that is not the ground truth, which is the
        // case they exist for.
        let not_self = evaluate(
            "Did rain occur from 15:00 to 16:00 UTC?",
            "Rain occurred from 15:00 to 16:00 UTC.",
            "Rain occurred from 17:00 to 18:00 UTC.",
        );
        assert_eq!(not_self.score, 0.0, "{not_self:?}");
        assert_ne!(not_self.issues & ISSUE_WRONG_TIME_WINDOW, 0);
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
