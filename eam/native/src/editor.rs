use crate::common::*;
use std::{
    fs::OpenOptions,
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt},
        io::AsRawFd,
    },
    path::Path,
    process::{Command, Stdio},
};
/// A single, shell-safe executable path also works with editors that split
/// VISUAL on whitespace instead of interpreting shell quoting.
pub fn launcher(runtime: &Path, executable: &Path, route: &Path) -> Result<String> {
    use std::io::Write;
    let path = runtime.join("editor");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o700)
        .open(&path)?;
    writeln!(
        file,
        "#!/bin/sh\nexec {} editor {} \"$@\"",
        shquote(&executable.to_string_lossy()),
        shquote(&route.to_string_lossy())
    )?;
    Ok(path.to_string_lossy().into_owned())
}

pub fn run(args: &[String]) -> Result<i32> {
    let (client, socket, files) = if args.first().is_some_and(|x| x == "--direct") {
        if args.len() < 4 {
            return Err(error("editor --direct CLIENT SOCKET FILE..."));
        }
        (args[1].clone(), args[2].clone(), &args[3..])
    } else {
        if args.len() < 2 {
            return Err(error("editor ROUTE FILE..."));
        }
        let p = Path::new(&args[0]);
        let m = std::fs::symlink_metadata(p)?;
        if m.uid() != unsafe { libc::getuid() } || m.mode() & 0o077 != 0 {
            return Err(error("Editor route must be private"));
        }
        let v = read_json(p, 4096)?;
        if v["version"] != 1 {
            return Err(error("Invalid editor route"));
        }
        if let Some(lease) = v["lease"].as_str() {
            let f = OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(lease)?;
            if unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
                return Err(error("No active Emacs attachment"));
            }
        }
        (
            string(&v, "client")?.to_owned(),
            string(&v, "socket")?.to_owned(),
            &args[1..],
        )
    };
    if !Path::new(&client).is_absolute() || !Path::new(&socket).is_absolute() {
        return Err(error("Absolute editor endpoint required"));
    }
    let paths: Vec<String> = files
        .iter()
        .map(|f| {
            if Path::new(f).is_absolute() {
                f.clone()
            } else {
                std::env::current_dir()
                    .unwrap()
                    .join(f)
                    .to_string_lossy()
                    .into_owned()
            }
        })
        .collect();
    let quoted: Vec<String> = paths
        .iter()
        .map(serde_json::to_string)
        .collect::<std::result::Result<_, _>>()?;
    let expr = format!(
        "(progn (eam-terminal--register-editor-files '({})) nil)",
        quoted.join(" ")
    );
    let base = ["--socket-name", &socket, "-a", "false"];
    let r = Command::new(&client)
        .args(base)
        .args(["--eval", &expr])
        .stdout(Stdio::null())
        .status()?;
    if !r.success() {
        return Ok(r.code().unwrap_or(1));
    }
    Ok(Command::new(&client)
        .args(base)
        .arg("--")
        .args(paths)
        .status()?
        .code()
        .unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn launcher_supports_direct_and_shell_invocation_with_quoted_paths() {
        let runtime = runtime_dir().unwrap();
        let executable = runtime.join("실행 파일 ' $ echo");
        std::os::unix::fs::symlink("/bin/echo", &executable).unwrap();
        let route = runtime.join("한글 ' $ route.json");
        let input = "입력 ' $(false) 파일.txt";
        let editor = launcher(&runtime, &executable, &route).unwrap();
        assert_eq!(editor.split_whitespace().count(), 1);
        assert_eq!(std::fs::metadata(&editor).unwrap().mode() & 0o777, 0o700);
        let expected = format!("editor {} {}\n", route.display(), input);
        let direct = Command::new(&editor).arg(input).output().unwrap();
        assert!(direct.status.success());
        assert_eq!(String::from_utf8(direct.stdout).unwrap(), expected);
        let shell = Command::new("/bin/sh")
            .args(["-c", &format!("{editor} \"$1\""), "sh", input])
            .output()
            .unwrap();
        assert!(shell.status.success());
        assert_eq!(String::from_utf8(shell.stdout).unwrap(), expected);
        std::fs::remove_dir_all(runtime).unwrap();
    }
}
