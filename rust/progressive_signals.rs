//! Process-local signal ownership for the external-worker supervisor. The
//! handler only stores a lock-free atomic; cleanup and receipts run normally.
use std::sync::{
    Mutex, MutexGuard,
    atomic::{AtomicI32, Ordering},
};
static OWNER: Mutex<()> = Mutex::new(());
static SIGNAL: AtomicI32 = AtomicI32::new(0);
extern "C" fn request_stop(signal: libc::c_int) {
    SIGNAL.store(signal, Ordering::Relaxed);
}
pub(super) struct Signals {
    _owner: MutexGuard<'static, ()>,
    previous: Vec<(i32, libc::sigaction)>,
}
impl Signals {
    pub fn install() -> Result<Self, String> {
        let owner = OWNER
            .try_lock()
            .map_err(|_| "only one progressive supervisor may own process signals")?;
        SIGNAL.store(0, Ordering::Relaxed);
        let mut result = Self {
            _owner: owner,
            previous: Vec::new(),
        };
        for signal in [libc::SIGINT, libc::SIGTERM] {
            let mut previous = unsafe { std::mem::zeroed() };
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            action.sa_sigaction = request_stop as *const () as usize;
            action.sa_flags = libc::SA_RESTART;
            unsafe {
                libc::sigemptyset(&mut action.sa_mask);
            }
            if unsafe { libc::sigaction(signal, &action, &mut previous) } != 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            result.previous.push((signal, previous));
        }
        Ok(result)
    }
    pub fn requested(&self) -> Option<i32> {
        match SIGNAL.load(Ordering::Relaxed) {
            0 => None,
            signal => Some(signal),
        }
    }
}
impl Drop for Signals {
    fn drop(&mut self) {
        for (signal, previous) in self.previous.iter().rev() {
            unsafe {
                libc::sigaction(*signal, previous, std::ptr::null_mut());
            }
        }
    }
}
