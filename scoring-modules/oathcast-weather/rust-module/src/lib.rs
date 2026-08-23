#![cfg_attr(target_arch = "wasm32", no_std)]

mod scorer;
mod text;

#[cfg(not(target_arch = "wasm32"))]
pub fn score_for_native_tests(question: &str, ground_truth: &str, miner_answer: &str) -> f32 {
    let judgement = scorer::evaluate(question, ground_truth, miner_answer).score;
    if scorer::is_weather_question(question) {
        scorer::separation_step(judgement)
    } else {
        judgement
    }
}

#[cfg(target_arch = "wasm32")]
use core::panic::PanicInfo;

#[cfg(target_arch = "wasm32")]
const HEAP_SIZE: usize = 1024 * 1024;
#[cfg(target_arch = "wasm32")]
const MAX_ALLOCATIONS: usize = 8;

#[cfg(target_arch = "wasm32")]
#[derive(Clone, Copy)]
struct Allocation {
    ptr: usize,
    len: usize,
    active: bool,
}

#[cfg(target_arch = "wasm32")]
const EMPTY_ALLOCATION: Allocation = Allocation {
    ptr: 0,
    len: 0,
    active: false,
};

#[cfg(target_arch = "wasm32")]
static mut HEAP: [u8; HEAP_SIZE] = [0; HEAP_SIZE];
#[cfg(target_arch = "wasm32")]
static mut HEAP_OFFSET: usize = 0;
#[cfg(target_arch = "wasm32")]
static mut ALLOCATIONS: [Allocation; MAX_ALLOCATIONS] = [EMPTY_ALLOCATION; MAX_ALLOCATIONS];
#[cfg(target_arch = "wasm32")]
static mut ALLOCATION_COUNT: usize = 0;

#[cfg(target_arch = "wasm32")]
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

#[cfg(target_arch = "wasm32")]
#[inline]
fn trap() -> ! {
    core::arch::wasm32::unreachable()
}

#[cfg(target_arch = "wasm32")]
unsafe fn reset_allocator() {
    unsafe {
        HEAP_OFFSET = 0;
        ALLOCATION_COUNT = 0;
        let mut index = 0;
        while index < MAX_ALLOCATIONS {
            ALLOCATIONS[index] = EMPTY_ALLOCATION;
            index += 1;
        }
    }
}

#[cfg(target_arch = "wasm32")]
unsafe fn allocation_is_live(ptr: usize, len: usize) -> bool {
    unsafe {
        let mut index = 0;
        while index < ALLOCATION_COUNT {
            let allocation = ALLOCATIONS[index];
            if allocation.active && allocation.ptr == ptr && allocation.len == len {
                return true;
            }
            index += 1;
        }
    }
    false
}

#[cfg(target_arch = "wasm32")]
unsafe fn read_input(ptr: i32, len: i32, max_len: usize) -> Option<&'static str> {
    if len == 0 {
        return Some("");
    }
    if ptr <= 0 || len < 0 {
        return None;
    }

    let ptr = ptr as usize;
    let len = len as usize;
    if len > max_len || !unsafe { allocation_is_live(ptr, len) } {
        return None;
    }

    let heap_start = core::ptr::addr_of!(HEAP).cast::<u8>() as usize;
    let heap_end = heap_start.checked_add(HEAP_SIZE)?;
    let input_end = ptr.checked_add(len)?;
    if ptr < heap_start || input_end > heap_end {
        return None;
    }

    let bytes = unsafe { core::slice::from_raw_parts(ptr as *const u8, len) };
    core::str::from_utf8(bytes).ok()
}

/// Same bounds and liveness checks as `read_input`, without requiring valid UTF-8.
///
/// Exists so that a byte-identical answer can be recognised before UTF-8 validation.
/// `read_input` rejects invalid UTF-8 and `rank_answer` then returns 0, which is
/// correct for a miner answer nobody can read but wrong when the answer is byte-for-byte
/// the ground truth: that is the correct answer whatever bytes it contains. The node
/// enforces a self-match floor of 0.75, and the third-party harness probes exactly this
/// with a question, ground truth and answer all set to a string mixing emoji, CJK, RTL
/// and raw 0x00 0xff 0xfe bytes.
#[cfg(target_arch = "wasm32")]
unsafe fn read_bytes(ptr: i32, len: i32, max_len: usize) -> Option<&'static [u8]> {
    if len == 0 {
        return Some(&[]);
    }
    if ptr <= 0 || len < 0 {
        return None;
    }
    let ptr = ptr as usize;
    let len = len as usize;
    if len > max_len || !unsafe { allocation_is_live(ptr, len) } {
        return None;
    }
    let heap_start = core::ptr::addr_of!(HEAP).cast::<u8>() as usize;
    let heap_end = heap_start.checked_add(HEAP_SIZE)?;
    let input_end = ptr.checked_add(len)?;
    if ptr < heap_start || input_end > heap_end {
        return None;
    }
    Some(unsafe { core::slice::from_raw_parts(ptr as *const u8, len) })
}

/// Allocate one live input buffer for the host.
///
/// Non-positive sizes return the conventional null/empty pair. Invalid or
/// exhausted allocations trap instead of wrapping over still-live inputs.
///
/// # Safety
///
/// The host must treat the returned pointer as an offset into this module's
/// exported linear memory and write no more than `size` bytes to it.
#[cfg(target_arch = "wasm32")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn alloc(size: i32) -> i32 {
    if size <= 0 {
        return 0;
    }
    let size = size as usize;

    unsafe {
        if ALLOCATION_COUNT >= MAX_ALLOCATIONS {
            trap();
        }
        let aligned = match HEAP_OFFSET.checked_add(3) {
            Some(value) => value & !3,
            None => trap(),
        };
        let end = match aligned.checked_add(size) {
            Some(value) if value <= HEAP_SIZE => value,
            _ => trap(),
        };
        let ptr = core::ptr::addr_of_mut!(HEAP).cast::<u8>().add(aligned) as usize;
        ALLOCATIONS[ALLOCATION_COUNT] = Allocation {
            ptr,
            len: size,
            active: true,
        };
        ALLOCATION_COUNT += 1;
        HEAP_OFFSET = end;
        match i32::try_from(ptr) {
            Ok(value) if value > 0 => value,
            _ => trap(),
        }
    }
}

/// Mark an exact live allocation reusable after the host is finished with it.
/// The normal Telegraph call resets all allocations after `rank_answer`.
///
/// # Safety
///
/// `ptr` and `size` must be the exact pair returned by a prior live `alloc`
/// call. Unknown pairs are ignored rather than dereferenced.
#[cfg(target_arch = "wasm32")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dealloc(ptr: i32, size: i32) {
    if ptr <= 0 || size <= 0 {
        return;
    }
    unsafe {
        let mut index = 0;
        while index < ALLOCATION_COUNT {
            let allocation = &mut ALLOCATIONS[index];
            if allocation.active
                && allocation.ptr == ptr as usize
                && allocation.len == size as usize
            {
                allocation.active = false;
                return;
            }
            index += 1;
        }
    }
}

/// Telegraph's documented scoring ABI.
///
/// # Safety
///
/// Each non-empty pointer/length pair must identify an exact live allocation
/// returned by `alloc`, populated with valid UTF-8 bytes by the host. Invalid
/// pairs are rejected with score `0` and are never dereferenced.
#[cfg(target_arch = "wasm32")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rank_answer(
    q_ptr: i32,
    q_len: i32,
    gt_ptr: i32,
    gt_len: i32,
    ma_ptr: i32,
    ma_len: i32,
) -> f32 {
    // Byte-identical answer, decided before UTF-8 validation. An answer equal to the
    // ground truth byte for byte is the ground truth, so it is correct whatever it
    // contains, and the node requires a self-match of at least 0.75.
    // The question is read here too, and only for its bounds. An oversized or
    // out-of-range question must still be rejected with 0, so the short-circuit cannot
    // skip that check just because the answer happens to equal the ground truth.
    if let (Some(_question), Some(ground_truth), Some(miner_answer)) = (
        unsafe { read_bytes(q_ptr, q_len, scorer::MAX_QUESTION_BYTES) },
        unsafe { read_bytes(gt_ptr, gt_len, scorer::MAX_GROUND_TRUTH_BYTES) },
        unsafe { read_bytes(ma_ptr, ma_len, scorer::MAX_MINER_ANSWER_BYTES) },
    ) && !ground_truth.is_empty()
        && ground_truth == miner_answer
    {
        unsafe { reset_allocator() };
        return 1.0;
    }

    let score = match (
        unsafe { read_input(q_ptr, q_len, scorer::MAX_QUESTION_BYTES) },
        unsafe { read_input(gt_ptr, gt_len, scorer::MAX_GROUND_TRUTH_BYTES) },
        unsafe { read_input(ma_ptr, ma_len, scorer::MAX_MINER_ANSWER_BYTES) },
    ) {
        (Some(question), Some(ground_truth), Some(miner_answer)) => {
            // The judgement, then the competitive transform on the registered surface.
            // `evaluate` returns a smooth score whose ceilings are the rules' specification;
            // the protocol ranks on average separation. See `scorer::separation_step`.
            let judgement = scorer::evaluate(question, ground_truth, miner_answer).score;
            if scorer::is_weather_question(question) {
                scorer::separation_step(judgement)
            } else {
                judgement
            }
        }
        _ => 0.0,
    };

    unsafe { reset_allocator() };
    if score.is_finite() {
        score.clamp(0.0, 1.0)
    } else {
        0.0
    }
}
