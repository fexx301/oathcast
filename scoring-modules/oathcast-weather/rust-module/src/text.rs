pub(crate) const MAX_JSON_DEPTH: usize = 24;

#[derive(Clone, Copy)]
pub(crate) struct Token<'a> {
    pub bytes: &'a [u8],
    pub end: usize,
}

pub(crate) struct TokenIter<'a> {
    bytes: &'a [u8],
    cursor: usize,
}

impl<'a> TokenIter<'a> {
    pub(crate) fn new(text: &'a str) -> Self {
        Self {
            bytes: text.as_bytes(),
            cursor: 0,
        }
    }
}

impl<'a> Iterator for TokenIter<'a> {
    type Item = Token<'a>;

    fn next(&mut self) -> Option<Self::Item> {
        while self.cursor < self.bytes.len() && !self.bytes[self.cursor].is_ascii_alphanumeric() {
            self.cursor += 1;
        }
        if self.cursor >= self.bytes.len() {
            return None;
        }

        let start = self.cursor;
        self.cursor += 1;
        while self.cursor < self.bytes.len() {
            let byte = self.bytes[self.cursor];
            if byte.is_ascii_alphanumeric() {
                self.cursor += 1;
                continue;
            }
            if byte == b'.'
                && self.cursor > start
                && self.cursor + 1 < self.bytes.len()
                && self.bytes[self.cursor - 1].is_ascii_digit()
                && self.bytes[self.cursor + 1].is_ascii_digit()
            {
                self.cursor += 1;
                continue;
            }
            break;
        }

        Some(Token {
            bytes: &self.bytes[start..self.cursor],
            end: self.cursor,
        })
    }
}

pub(crate) fn token_eq(token: &[u8], expected: &[u8]) -> bool {
    token.eq_ignore_ascii_case(expected)
}

pub(crate) fn hash_lower(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    for byte in bytes {
        hash ^= byte.to_ascii_lowercase() as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn in_group(token: &[u8], values: &[&[u8]]) -> bool {
    values.iter().any(|value| token_eq(token, value))
}

pub(crate) fn semantic_hash(token: &[u8]) -> Option<u64> {
    const STOPWORDS: &[&[u8]] = &[
        b"a",
        b"an",
        b"and",
        b"answer",
        b"assistant",
        b"at",
        b"be",
        b"between",
        b"by",
        b"choices",
        b"content",
        b"did",
        b"during",
        b"for",
        b"from",
        b"greater",
        b"in",
        b"is",
        b"it",
        b"less",
        b"message",
        b"mm",
        b"no",
        b"occur",
        b"occurred",
        b"of",
        b"on",
        b"or",
        b"probability",
        b"requested",
        b"response",
        b"role",
        b"text",
        b"the",
        b"to",
        b"true",
        b"utc",
        b"was",
        b"weather",
        b"will",
        b"yes",
    ];
    if in_group(token, STOPWORDS) {
        return None;
    }

    const PRECIPITATION: &[&[u8]] = &[
        b"drizzle",
        b"drizzling",
        b"precip",
        b"precipitation",
        b"rain",
        b"rainfall",
        b"raining",
        b"shower",
        b"showers",
    ];
    const TEMPERATURE: &[&[u8]] = &[b"temp", b"temperature", b"temperatures"];
    const WIND: &[&[u8]] = &[b"breeze", b"breezy", b"wind", b"winds", b"windy"];
    const GUST: &[&[u8]] = &[b"gust", b"gusting", b"gusts"];
    const CLOUD: &[&[u8]] = &[b"cloud", b"clouds", b"cloudy", b"overcast"];
    const CLEAR: &[&[u8]] = &[b"clear", b"clearing", b"sun", b"sunny", b"sunshine"];
    const SNOW: &[&[u8]] = &[b"flurries", b"snow", b"snowfall", b"snowing"];
    const STORM: &[&[u8]] = &[b"storm", b"storms", b"thunderstorm", b"thunderstorms"];
    const HUMIDITY: &[&[u8]] = &[b"humid", b"humidity"];
    const FOG: &[&[u8]] = &[b"fog", b"foggy", b"mist", b"misty"];
    const PRESSURE: &[&[u8]] = &[b"barometric", b"pressure"];
    const VISIBILITY: &[&[u8]] = &[b"visibility", b"visible"];
    const ICE: &[&[u8]] = &[b"freeze", b"freezing", b"frost", b"ice", b"icy"];
    const HAIL: &[&[u8]] = &[b"hail", b"hailing", b"hailstone", b"hailstones"];
    const MAXIMUM: &[&[u8]] = &[b"high", b"max", b"maximum", b"peak"];
    const MINIMUM: &[&[u8]] = &[b"low", b"min", b"minimum"];

    let canonical = if in_group(token, PRECIPITATION) {
        b"precipitation".as_slice()
    } else if in_group(token, TEMPERATURE) {
        b"temperature".as_slice()
    } else if in_group(token, WIND) {
        b"wind".as_slice()
    } else if in_group(token, GUST) {
        b"gust".as_slice()
    } else if in_group(token, CLOUD) {
        b"cloud".as_slice()
    } else if in_group(token, CLEAR) {
        b"clear".as_slice()
    } else if in_group(token, SNOW) {
        b"snow".as_slice()
    } else if in_group(token, STORM) {
        b"storm".as_slice()
    } else if in_group(token, HUMIDITY) {
        b"humidity".as_slice()
    } else if in_group(token, FOG) {
        b"fog".as_slice()
    } else if in_group(token, PRESSURE) {
        b"pressure".as_slice()
    } else if in_group(token, VISIBILITY) {
        b"visibility".as_slice()
    } else if in_group(token, ICE) {
        b"ice".as_slice()
    } else if in_group(token, HAIL) {
        b"hail".as_slice()
    } else if in_group(token, MAXIMUM) {
        b"maximum".as_slice()
    } else if in_group(token, MINIMUM) {
        b"minimum".as_slice()
    } else {
        token
    };
    Some(hash_lower(canonical))
}

/// Weather concepts, each listed with the everyday words and unit abbreviations that
/// express it.
///
/// The canonical sixteen were matched by exact token, so "rain" scored no agreement at
/// all against a ground truth saying "precipitation", and "29.4 C" scored none against
/// "29.4 degrees Celsius". Both are the plain way to answer a weather question, and both
/// pulled `concept_quality` to 0.0, which drags `factual` down and costs separation on
/// correct answers. An exact temperature match measured 0.4712 this way.
///
/// Morphological variants are spelled out rather than stemmed, because the token
/// comparison is an exact hash and a stemmer is a great deal of machinery for a closed
/// vocabulary.
pub(crate) fn weather_concept_mask(text: &str) -> u32 {
    const PRECIPITATION: u32 = 1 << 0;
    const TEMPERATURE: u32 = 1 << 1;
    const CONCEPTS: &[(&[u8], u32)] = &[
        (b"precipitation", PRECIPITATION),
        (b"rain", PRECIPITATION),
        (b"rains", PRECIPITATION),
        (b"rained", PRECIPITATION),
        (b"raining", PRECIPITATION),
        (b"rainfall", PRECIPITATION),
        (b"rainy", PRECIPITATION),
        (b"drizzle", PRECIPITATION),
        (b"shower", PRECIPITATION),
        (b"showers", PRECIPITATION),
        (b"wet", PRECIPITATION),
        (b"mm", PRECIPITATION),
        (b"temperature", TEMPERATURE),
        (b"temperatures", TEMPERATURE),
        (b"degrees", TEMPERATURE),
        (b"degree", TEMPERATURE),
        (b"celsius", TEMPERATURE),
        (b"centigrade", TEMPERATURE),
        (b"fahrenheit", TEMPERATURE),
        (b"kelvin", TEMPERATURE),
        (b"warm", TEMPERATURE),
        (b"warmer", TEMPERATURE),
        (b"cold", TEMPERATURE),
        (b"colder", TEMPERATURE),
        (b"cool", TEMPERATURE),
        (b"cooler", TEMPERATURE),
        (b"hot", TEMPERATURE),
        (b"hotter", TEMPERATURE),
        (b"mild", TEMPERATURE),
        (b"c", TEMPERATURE),
        (b"f", TEMPERATURE),
        (b"k", TEMPERATURE),
        (b"wind", 1 << 2),
        (b"winds", 1 << 2),
        (b"windy", 1 << 2),
        (b"breeze", 1 << 2),
        (b"breezy", 1 << 2),
        (b"gust", 1 << 3),
        (b"gusts", 1 << 3),
        (b"gusting", 1 << 3),
        (b"cloud", 1 << 4),
        (b"clouds", 1 << 4),
        (b"cloudy", 1 << 4),
        (b"overcast", 1 << 4),
        (b"clear", 1 << 5),
        (b"sunny", 1 << 5),
        (b"sun", 1 << 5),
        (b"sunshine", 1 << 5),
        (b"snow", 1 << 6),
        (b"snowfall", 1 << 6),
        (b"snowing", 1 << 6),
        (b"sleet", 1 << 6),
        (b"storm", 1 << 7),
        (b"storms", 1 << 7),
        (b"stormy", 1 << 7),
        (b"thunderstorm", 1 << 7),
        (b"thunder", 1 << 7),
        (b"squall", 1 << 7),
        (b"humidity", 1 << 8),
        (b"humid", 1 << 8),
        (b"fog", 1 << 9),
        (b"foggy", 1 << 9),
        (b"mist", 1 << 9),
        (b"misty", 1 << 9),
        (b"haze", 1 << 9),
        (b"pressure", 1 << 10),
        (b"hpa", 1 << 10),
        (b"millibar", 1 << 10),
        (b"millibars", 1 << 10),
        (b"visibility", 1 << 11),
        (b"ice", 1 << 12),
        (b"icy", 1 << 12),
        (b"frost", 1 << 12),
        (b"freezing", 1 << 12),
        (b"hail", 1 << 13),
        (b"maximum", 1 << 14),
        (b"minimum", 1 << 15),
    ];
    let mut mask = 0u32;
    for token in TokenIter::new(text) {
        let Some(hash) = semantic_hash(token.bytes) else {
            continue;
        };
        for (canonical, bit) in CONCEPTS {
            if hash == hash_lower(canonical) {
                mask |= bit;
                break;
            }
        }
    }
    mask
}

pub(crate) fn parse_decimal(bytes: &[u8]) -> Option<f32> {
    if bytes.is_empty() {
        return None;
    }
    let mut index = 0;
    let mut negative = false;
    if bytes[index] == b'-' {
        negative = true;
        index += 1;
    } else if bytes[index] == b'+' {
        index += 1;
    }
    if index >= bytes.len() {
        return None;
    }

    let mut value = 0.0f32;
    let mut digits = 0usize;
    while index < bytes.len() && bytes[index].is_ascii_digit() {
        value = (value * 10.0) + (bytes[index] - b'0') as f32;
        digits += 1;
        index += 1;
    }

    if index < bytes.len() && bytes[index] == b'.' {
        index += 1;
        let mut place = 0.1f32;
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            value += (bytes[index] - b'0') as f32 * place;
            place *= 0.1;
            digits += 1;
            index += 1;
        }
    }
    if digits == 0 || index != bytes.len() {
        return None;
    }
    Some(if negative { -value } else { value })
}

struct JsonParser<'a> {
    bytes: &'a [u8],
    cursor: usize,
}

impl<'a> JsonParser<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            bytes: text.as_bytes(),
            cursor: 0,
        }
    }

    fn skip_ws(&mut self) {
        while self.cursor < self.bytes.len() && self.bytes[self.cursor].is_ascii_whitespace() {
            self.cursor += 1;
        }
    }

    fn parse(mut self) -> bool {
        self.skip_ws();
        if !self.value(0) {
            return false;
        }
        self.skip_ws();
        self.cursor == self.bytes.len()
    }

    fn value(&mut self, depth: usize) -> bool {
        if depth > MAX_JSON_DEPTH {
            return false;
        }
        self.skip_ws();
        let Some(byte) = self.bytes.get(self.cursor).copied() else {
            return false;
        };
        match byte {
            b'{' => self.object(depth + 1),
            b'[' => self.array(depth + 1),
            b'"' => self.string().is_some(),
            b't' => self.literal(b"true"),
            b'f' => self.literal(b"false"),
            b'n' => self.literal(b"null"),
            b'-' | b'0'..=b'9' => self.number(),
            _ => false,
        }
    }

    fn object(&mut self, depth: usize) -> bool {
        self.cursor += 1;
        self.skip_ws();
        if self.consume(b'}') {
            return true;
        }
        loop {
            self.skip_ws();
            if self.string().is_none() {
                return false;
            }
            self.skip_ws();
            if !self.consume(b':') || !self.value(depth) {
                return false;
            }
            self.skip_ws();
            if self.consume(b'}') {
                return true;
            }
            if !self.consume(b',') {
                return false;
            }
        }
    }

    fn array(&mut self, depth: usize) -> bool {
        self.cursor += 1;
        self.skip_ws();
        if self.consume(b']') {
            return true;
        }
        loop {
            if !self.value(depth) {
                return false;
            }
            self.skip_ws();
            if self.consume(b']') {
                return true;
            }
            if !self.consume(b',') {
                return false;
            }
        }
    }

    fn string(&mut self) -> Option<(usize, usize)> {
        if !self.consume(b'"') {
            return None;
        }
        let start = self.cursor;
        while self.cursor < self.bytes.len() {
            match self.bytes[self.cursor] {
                b'"' => {
                    let end = self.cursor;
                    self.cursor += 1;
                    return Some((start, end));
                }
                b'\\' => {
                    self.cursor += 1;
                    let escaped = *self.bytes.get(self.cursor)?;
                    match escaped {
                        b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => {
                            self.cursor += 1;
                        }
                        b'u' => {
                            self.cursor += 1;
                            for _ in 0..4 {
                                if !self.bytes.get(self.cursor)?.is_ascii_hexdigit() {
                                    return None;
                                }
                                self.cursor += 1;
                            }
                        }
                        _ => return None,
                    }
                }
                0x00..=0x1f => return None,
                _ => self.cursor += 1,
            }
        }
        None
    }

    fn number(&mut self) -> bool {
        let start = self.cursor;
        self.consume(b'-');
        match self.bytes.get(self.cursor).copied() {
            Some(b'0') => self.cursor += 1,
            Some(b'1'..=b'9') => {
                self.cursor += 1;
                while self.bytes.get(self.cursor).is_some_and(u8::is_ascii_digit) {
                    self.cursor += 1;
                }
            }
            _ => return false,
        }
        if self.consume(b'.') {
            let fraction_start = self.cursor;
            while self.bytes.get(self.cursor).is_some_and(u8::is_ascii_digit) {
                self.cursor += 1;
            }
            if self.cursor == fraction_start {
                return false;
            }
        }
        if matches!(self.bytes.get(self.cursor), Some(b'e' | b'E')) {
            self.cursor += 1;
            if matches!(self.bytes.get(self.cursor), Some(b'+' | b'-')) {
                self.cursor += 1;
            }
            let exponent_start = self.cursor;
            while self.bytes.get(self.cursor).is_some_and(u8::is_ascii_digit) {
                self.cursor += 1;
            }
            if self.cursor == exponent_start {
                return false;
            }
        }
        self.cursor > start
    }

    fn literal(&mut self, expected: &[u8]) -> bool {
        let end = match self.cursor.checked_add(expected.len()) {
            Some(value) => value,
            None => return false,
        };
        if self.bytes.get(self.cursor..end) == Some(expected) {
            self.cursor = end;
            true
        } else {
            false
        }
    }

    fn consume(&mut self, expected: u8) -> bool {
        if self.bytes.get(self.cursor) == Some(&expected) {
            self.cursor += 1;
            true
        } else {
            false
        }
    }
}

pub(crate) fn is_json_like(text: &str) -> bool {
    matches!(text.trim_start().as_bytes().first(), Some(b'{' | b'['))
}

pub(crate) fn is_valid_json(text: &str) -> bool {
    JsonParser::new(text).parse()
}

fn raw_json_string_eq(raw: &[u8], expected: &[u8]) -> bool {
    !raw.contains(&b'\\') && raw == expected
}

fn skip_json_string(bytes: &[u8], mut cursor: usize) -> Option<(usize, usize, usize)> {
    if bytes.get(cursor) != Some(&b'"') {
        return None;
    }
    cursor += 1;
    let start = cursor;
    while cursor < bytes.len() {
        match bytes[cursor] {
            b'"' => return Some((start, cursor, cursor + 1)),
            b'\\' => {
                cursor += 1;
                match bytes.get(cursor).copied()? {
                    b'u' => cursor = cursor.checked_add(5)?,
                    _ => cursor += 1,
                }
            }
            _ => cursor += 1,
        }
    }
    None
}

fn skip_ws(bytes: &[u8], mut cursor: usize) -> usize {
    while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
        cursor += 1;
    }
    cursor
}

pub(crate) fn find_json_string_field<'a>(text: &'a str, key: &[u8]) -> Option<&'a str> {
    let bytes = text.as_bytes();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if bytes[cursor] != b'"' {
            cursor += 1;
            continue;
        }
        let (start, end, after) = skip_json_string(bytes, cursor)?;
        cursor = after;
        if !raw_json_string_eq(&bytes[start..end], key) {
            continue;
        }
        let colon = skip_ws(bytes, cursor);
        if bytes.get(colon) != Some(&b':') {
            continue;
        }
        let value = skip_ws(bytes, colon + 1);
        let (value_start, value_end, _after_value) = match skip_json_string(bytes, value) {
            Some(parts) => parts,
            None => continue,
        };
        return text.get(value_start..value_end);
    }
    None
}

pub(crate) fn select_scoring_text(text: &str) -> &str {
    if !is_json_like(text) {
        return text.trim();
    }
    for key in [
        b"content".as_slice(),
        b"answer".as_slice(),
        b"text".as_slice(),
    ] {
        if let Some(value) = find_json_string_field(text, key) {
            return value;
        }
    }
    text.trim()
}

fn find_json_number_field(text: &str, key: &[u8]) -> Option<f32> {
    let bytes = text.as_bytes();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if bytes[cursor] != b'"' {
            cursor += 1;
            continue;
        }
        let (start, end, after) = skip_json_string(bytes, cursor)?;
        cursor = after;
        if !raw_json_string_eq(&bytes[start..end], key) {
            continue;
        }
        let colon = skip_ws(bytes, cursor);
        if bytes.get(colon) != Some(&b':') {
            continue;
        }
        let value_start = skip_ws(bytes, colon + 1);
        let mut value_end = value_start;
        if matches!(bytes.get(value_end), Some(b'+' | b'-')) {
            value_end += 1;
        }
        while matches!(bytes.get(value_end), Some(b'0'..=b'9' | b'.')) {
            value_end += 1;
        }
        if value_end > value_start {
            return parse_decimal(&bytes[value_start..value_end]);
        }
    }
    None
}

/// Small cardinal written as a word, plus the articles that stand for one.
fn number_word(token: &[u8]) -> Option<f32> {
    const VALUES: &[(&[u8], f32)] = &[
        (b"a", 1.0),
        (b"an", 1.0),
        (b"one", 1.0),
        (b"two", 2.0),
        (b"three", 3.0),
        (b"four", 4.0),
        (b"five", 5.0),
        (b"six", 6.0),
        (b"seven", 7.0),
        (b"eight", 8.0),
        (b"nine", 9.0),
        (b"ten", 10.0),
        (b"eleven", 11.0),
        (b"twelve", 12.0),
    ];
    VALUES
        .iter()
        .find(|(word, _)| token_eq_lower(token, word))
        .map(|(_, value)| *value)
}

/// Denominator written as an ordinal or a fraction noun.
fn denominator_word(token: &[u8]) -> Option<f32> {
    const VALUES: &[(&[u8], f32)] = &[
        (b"half", 2.0),
        (b"halves", 2.0),
        (b"third", 3.0),
        (b"thirds", 3.0),
        (b"quarter", 4.0),
        (b"quarters", 4.0),
        (b"fourth", 4.0),
        (b"fourths", 4.0),
        (b"fifth", 5.0),
        (b"fifths", 5.0),
        (b"sixth", 6.0),
        (b"sixths", 6.0),
        (b"eighth", 8.0),
        (b"eighths", 8.0),
        (b"tenth", 10.0),
        (b"tenths", 10.0),
    ];
    VALUES
        .iter()
        .find(|(word, _)| token_eq_lower(token, word))
        .map(|(_, value)| *value)
}

/// A probability written in words rather than digits.
///
/// Recognises "one chance in five", "two chances in three", "one in five", "two thirds" and a
/// bare "half". Without this, a correct qualitative forecast states no probability at all as
/// far as the scorer is concerned, and `evaluate` applies the missing-probability ceiling of
/// 0.49 to it. Against a ground truth of "There is a 20% probability", the correct "Unlikely,
/// around one chance in five." was capped at 0.3272 and, once the output transform was
/// applied, landed at 0.0327 against a wrong answer's 0.0242: a pair separated by eight
/// thousandths where the answer is exactly right.
fn worded_probability(text: &str) -> Option<f32> {
    let mut pending: Option<f32> = None;
    let mut saw_chance = false;
    let mut best: Option<f32> = None;
    for token in TokenIter::new(text) {
        if let Some(denominator) = denominator_word(token.bytes) {
            // "two thirds", or a bare "half".
            let numerator = pending.unwrap_or(1.0);
            if denominator > 0.0 && numerator < denominator {
                best = Some(numerator / denominator);
            }
            pending = None;
            saw_chance = false;
            continue;
        }
        if token_eq_lower(token.bytes, b"chance") || token_eq_lower(token.bytes, b"chances") {
            saw_chance = true;
            continue;
        }
        if token_eq_lower(token.bytes, b"in") || token_eq_lower(token.bytes, b"of") {
            continue;
        }
        if let Some(value) = number_word(token.bytes) {
            match pending {
                // A second cardinal after the first closes the pattern: "one ... five".
                Some(numerator) if numerator < value => {
                    best = Some(numerator / value);
                    pending = None;
                    saw_chance = false;
                }
                _ => pending = Some(value),
            }
            continue;
        }
        // Any other content word breaks the pattern, unless a "chance" is still open.
        if !saw_chance {
            pending = None;
        }
    }
    best
}

/// True when a spelled-out percent marker starts at `at`, on a word boundary:
/// "percent", "per cent" and "pct". Registration 506 lost on separation, and this is
/// one measured reason. Only the '%' byte was recognised, so against a ground truth of
/// "There is a 65% probability", the answers "Roughly a 65 percent chance" and "Roughly
/// a 5 percent chance" both parsed as stating no probability at all, contributing 0.0
/// to the agreement term alike. The two scored 0.4739 and 0.4714, a separation of
/// 0.0025 between a correct forecast and one off by sixty points.
fn spelled_percent_len(bytes: &[u8], at: usize) -> Option<usize> {
    if at > 0 && (bytes[at - 1].is_ascii_alphabetic() || bytes[at - 1] == b'_') {
        return None;
    }
    for marker in [
        b"percent".as_slice(),
        b"per cent".as_slice(),
        b"pct".as_slice(),
    ] {
        let Some(window) = bytes.get(at..at + marker.len()) else {
            continue;
        };
        if window
            .iter()
            .zip(marker)
            .all(|(left, right)| left.to_ascii_lowercase() == *right)
            && !bytes
                .get(at + marker.len())
                .is_some_and(|next| next.is_ascii_alphabetic())
        {
            return Some(marker.len());
        }
    }
    None
}

fn percent_probability(text: &str) -> Option<f32> {
    let bytes = text.as_bytes();
    for percent in 0..bytes.len() {
        if bytes[percent] != b'%' && spelled_percent_len(bytes, percent).is_none() {
            continue;
        }
        let mut end = percent;
        while end > 0 && bytes[end - 1].is_ascii_whitespace() {
            end -= 1;
        }
        let mut start = end;
        while start > 0 && (bytes[start - 1].is_ascii_digit() || bytes[start - 1] == b'.') {
            start -= 1;
        }
        // Keep scanning past a '%' that carries no parseable number, exactly as
        // the out-of-range branch below already does. Abandoning the whole
        // function here instead let a bare "Humidity (%)" earlier in an answer
        // hide a real "70%" later in it, which drops a correct answer to the
        // 0.49 missing-probability ceiling in `scorer::evaluate`.
        let Some(value) = parse_decimal(&bytes[start..end]) else {
            continue;
        };
        if (0.0..=100.0).contains(&value) {
            return Some(value / 100.0);
        }
    }
    None
}

pub(crate) fn probability(text: &str) -> Option<f32> {
    if is_json_like(text) {
        for key in [
            b"probability".as_slice(),
            b"precipitation_probability".as_slice(),
            b"probability_of_precipitation".as_slice(),
            b"rain_probability".as_slice(),
            b"pop".as_slice(),
        ] {
            if let Some(value) = find_json_number_field(text, key) {
                if (0.0..=1.0).contains(&value) {
                    return Some(value);
                }
                if (1.0..=100.0).contains(&value) {
                    return Some(value / 100.0);
                }
            }
        }
    }
    percent_probability(text).or_else(|| worded_probability(text))
}

fn parse_time_at(bytes: &[u8], start: usize) -> Option<(u16, usize)> {
    if start > 0 && bytes[start - 1].is_ascii_digit() {
        return None;
    }
    let mut cursor = start;
    let mut hour = 0u16;
    let mut hour_digits = 0usize;
    while cursor < bytes.len() && bytes[cursor].is_ascii_digit() && hour_digits < 2 {
        hour = hour * 10 + (bytes[cursor] - b'0') as u16;
        hour_digits += 1;
        cursor += 1;
    }
    if hour_digits == 0 || bytes.get(cursor) != Some(&b':') {
        return None;
    }
    cursor += 1;
    let minute_tens = *bytes.get(cursor)?;
    let minute_ones = *bytes.get(cursor + 1)?;
    if !minute_tens.is_ascii_digit() || !minute_ones.is_ascii_digit() {
        return None;
    }
    let minute = ((minute_tens - b'0') as u16 * 10) + (minute_ones - b'0') as u16;
    cursor += 2;
    if bytes.get(cursor).is_some_and(u8::is_ascii_digit) || hour > 23 || minute > 59 {
        return None;
    }
    Some((hour * 60 + minute, cursor))
}

fn contains_time(text: &str, expected: u16) -> bool {
    let bytes = text.as_bytes();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if let Some((value, end)) = parse_time_at(bytes, cursor) {
            if value == expected {
                return true;
            }
            cursor = end;
        } else {
            cursor += 1;
        }
    }
    false
}

fn contains_any_time(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if parse_time_at(bytes, cursor).is_some() {
            return true;
        }
        cursor += 1;
    }
    false
}

/// True when the question asks about a window and the answer designates the window's
/// closing time as the hour it is about, without naming the opening time.
///
/// `has_time_outside_question` cannot see this. Asked "from 15:00 to 16:00 UTC", an
/// answer about "the 16:00 UTC hour" names a time that does appear in the question, so
/// nothing is outside it, yet "the 16:00 hour" runs from 16:00 to 17:00 and is a
/// different window from the one requested. Measured before this check, the correct and
/// the shifted answer scored identically, and an exact tie loses a fixture case as
/// surely as an inversion.
///
/// Requires the answer to name the closing time and NOT the opening one, so an answer
/// that spans the window properly, "between 15:00 and 16:00", is untouched, and so is
/// an answer that names no time at all.
pub(crate) fn answer_binds_window_end_only(question: &str, answer: &str) -> bool {
    let mut times = [0u16; 8];
    let mut count = 0usize;
    let bytes = question.as_bytes();
    let mut cursor = 0usize;
    while cursor < bytes.len() && count < times.len() {
        if let Some((value, end)) = parse_time_at(bytes, cursor) {
            if !times[..count].contains(&value) {
                times[count] = value;
                count += 1;
            }
            cursor = end;
        } else {
            cursor += 1;
        }
    }
    if count < 2 {
        return false;
    }
    let start = times[0];
    let end = times[count - 1];
    if start == end {
        return false;
    }
    contains_time(answer, end) && !contains_time(answer, start)
}
/// Minutes between the first and last clock time a question names, when it names at
/// least two. This is the window the question asks about.
pub(crate) fn question_window_minutes(question: &str) -> Option<u32> {
    let bytes = question.as_bytes();
    let mut first: Option<u16> = None;
    let mut last: Option<u16> = None;
    let mut cursor = 0usize;
    while cursor < bytes.len() {
        if let Some((value, end)) = parse_time_at(bytes, cursor) {
            if first.is_none() {
                first = Some(value);
            } else {
                last = Some(value);
            }
            cursor = end;
        } else {
            cursor += 1;
        }
    }
    let (first, last) = (first?, last?);
    if last <= first {
        return None;
    }
    Some(u32::from(last - first))
}

/// The longest duration the text asserts, in minutes, from a figure followed by a unit
/// of time. Digits only; a spelled-out "twenty four hours" is not read.
pub(crate) fn asserted_duration_minutes(text: &str) -> Option<u32> {
    const UNITS: &[(&[u8], u32)] = &[
        (b"minute", 1),
        (b"minutes", 1),
        (b"hour", 60),
        (b"hours", 60),
        (b"day", 1440),
        (b"days", 1440),
        (b"week", 10080),
        (b"weeks", 10080),
    ];
    let mut longest: Option<u32> = None;
    let mut pending: Option<f32> = None;
    for token in TokenIter::new(text) {
        if let Some(value) = parse_decimal(token.bytes) {
            pending = Some(value);
            continue;
        }
        if let Some(value) = pending.take()
            && let Some((_, per)) = UNITS
                .iter()
                .find(|(unit, _)| token_eq_lower(token.bytes, unit))
        {
            let minutes = (value * *per as f32) as u32;
            if longest.is_none_or(|current| minutes > current) {
                longest = Some(minutes);
            }
        }
    }
    longest
}

/// True when the answer asserts a calendar year that neither the question nor the ground
/// truth mentions.
///
/// A forecast question fixes its own window, so a date from outside it is unsupported by
/// anything the scorer was given. The wrong answer "Yes, measurable precipitation
/// occurred during the requested hour on 2019-04-02." scored 0.9278 against a correct
/// 0.9776, because nothing looked at the date at all. This mirrors the existing
/// treatment of unsupported rank modifiers rather than inventing a new principle.
pub(crate) fn asserts_unsupported_year(question: &str, ground_truth: &str, answer: &str) -> bool {
    let known = |year: u32| {
        [question, ground_truth]
            .iter()
            .any(|source| text_contains_year(source, year))
    };
    for token in TokenIter::new(answer) {
        if let Some(year) = years_in(token.bytes)
            && !known(year)
        {
            return true;
        }
    }
    false
}

fn years_in(token: &[u8]) -> Option<u32> {
    let mut index = 0usize;
    while index + 4 <= token.len() {
        if token[index..index + 4].iter().all(u8::is_ascii_digit)
            && (index == 0 || !token[index - 1].is_ascii_digit())
            && token
                .get(index + 4)
                .is_none_or(|next| !next.is_ascii_digit())
        {
            let year = token[index..index + 4]
                .iter()
                .fold(0u32, |total, digit| total * 10 + u32::from(digit - b'0'));
            if (1800..=2200).contains(&year) {
                return Some(year);
            }
        }
        index += 1;
    }
    None
}

fn text_contains_year(text: &str, year: u32) -> bool {
    TokenIter::new(text).any(|token| years_in(token.bytes) == Some(year))
}

fn token_eq_lower(token: &[u8], other: &[u8]) -> bool {
    token.len() == other.len()
        && token
            .iter()
            .zip(other)
            .all(|(left, right)| left.to_ascii_lowercase() == *right)
}
pub(crate) fn has_time_outside_question(question: &str, answer: &str) -> bool {
    if !contains_any_time(question) {
        return false;
    }
    let bytes = answer.as_bytes();
    let mut cursor = 0;
    while cursor < bytes.len() {
        if let Some((value, end)) = parse_time_at(bytes, cursor) {
            if !contains_time(question, value) {
                return true;
            }
            cursor = end;
        } else {
            cursor += 1;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_json_and_extracts_nested_content() {
        let raw = r#"{"choices":[{"message":{"role":"assistant","content":"Rain is likely at 15:00 UTC."}}]}"#;
        assert!(is_valid_json(raw));
        assert_eq!(select_scoring_text(raw), "Rain is likely at 15:00 UTC.");
        assert!(!is_valid_json(r#"{"content":"unterminated}"#));
    }

    #[test]
    fn extracts_json_and_percent_probabilities() {
        assert_eq!(
            probability(r#"{"probability":0.7,"content":"Yes"}"#),
            Some(0.7)
        );
        assert_eq!(probability("There is a 65% chance."), Some(0.65));
    }

    #[test]
    fn a_percent_sign_without_a_number_does_not_hide_a_later_probability() {
        // A '%' with nothing parseable before it must not abandon the scan. When
        // it did, `scorer::evaluate` saw no probability in the answer and capped
        // a materially correct response at 0.49.
        assert_eq!(
            probability("Humidity (%). Rain probability: 70%."),
            Some(0.7)
        );
        assert_eq!(probability("% chance. Rain probability: 70%"), Some(0.7));
        assert_eq!(probability("20.5.5% noise, rain 70%"), Some(0.7));
        // An out-of-range value already fell through to the next '%'; keep that.
        assert_eq!(probability("150% impossible, rain 70%"), Some(0.7));
        // A '%' that genuinely carries no probability anywhere still yields None.
        assert_eq!(probability("Humidity (%) was not recorded."), None);
    }

    #[test]
    fn canonicalizes_common_weather_terms() {
        assert_eq!(semantic_hash(b"rain"), semantic_hash(b"precipitation"));
        assert_eq!(semantic_hash(b"temp"), semantic_hash(b"temperature"));
        assert_ne!(semantic_hash(b"wind"), semantic_hash(b"temperature"));
    }

    #[test]
    fn detects_times_not_present_in_the_question() {
        let question = "Forecast Lagos from 15:00 to 16:00 UTC";
        assert!(!has_time_outside_question(
            question,
            "Rain is likely from 15:00 to 16:00 UTC"
        ));
        assert!(has_time_outside_question(
            question,
            "Rain is likely from 10:00 to 11:00 UTC"
        ));
        assert!(!has_time_outside_question(
            "What is tomorrow's weather?",
            "Rain is likely around 15:00 UTC"
        ));
    }
}
