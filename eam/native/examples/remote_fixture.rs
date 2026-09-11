//! Fake CLI for remote-web integration tests, never included in installed packages.
use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
static PAINT: AtomicBool = AtomicBool::new(false);
static RESIZED: AtomicBool = AtomicBool::new(false);
extern "C" fn resized(_: i32) {
    RESIZED.store(true, Ordering::Relaxed);
}
fn paint() {
    print!("\x1b[2J\x1b[HFULL-SCREEN-RESTORED\r\n> ");
    io::stdout().flush().unwrap();
}
fn main() {
    unsafe {
        libc::signal(libc::SIGWINCH, resized as *const () as usize);
    }
    std::thread::spawn(|| loop {
        if RESIZED.swap(false, Ordering::Relaxed) && PAINT.load(Ordering::Relaxed) {
            paint();
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    });
    println!("\x1b[36mEAM remote test · 한글 입력 / approval / burst / size / exit\x1b[0m");
    io::stdout().flush().unwrap();
    for line in io::stdin().lock().lines() {
        match line.unwrap().trim() {
            "exit" => break,
            "approval" => println!("Approve file edit? [y/n]"),
            "size" => unsafe {
                let mut size: libc::winsize = std::mem::zeroed();
                libc::ioctl(0, libc::TIOCGWINSZ, &mut size);
                println!("SIZE columns={}, lines={}", size.ws_col, size.ws_row);
            },
            "overflow-screen" => {
                // Overflow replay without adding millions of visible glyphs.
                print!("{}", "\x1b[0m".repeat(1_400_000));
                PAINT.store(true, Ordering::Relaxed);
                paint();
            }
            "colors" => {
                println!("\x1b[37mANSI white · 한글\x1b[0m");
                println!("\x1b[97mANSI bright white · 한글\x1b[0m");
                println!("\x1b[93mANSI yellow · 한글\x1b[0m");
                println!("\x1b[38;5;250m256-color gray · 한글\x1b[0m");
                println!("\x1b[38;2;245;245;245mRGB near-white · 한글\x1b[0m");
                println!("\x1b[38;2;245;245;245;48;2;255;255;255mRGB white background\x1b[0m");
                println!("COLORS-DONE");
            }
            "burst" => {
                for i in 0..12000 {
                    println!("{i:05} 한글 streaming output {}", "x".repeat(80));
                }
                println!("BURST-DONE");
            }
            line => println!("REPLY: {line}"),
        }
        io::stdout().flush().unwrap();
    }
}
