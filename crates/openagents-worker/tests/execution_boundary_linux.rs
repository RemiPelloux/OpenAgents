#![cfg(target_os = "linux")]

use std::{
    env,
    fs::{self, OpenOptions},
    io::Write,
    os::unix::process::CommandExt,
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn sandbox() -> &'static str {
    env!("CARGO_BIN_EXE_openagents-sandbox")
}

fn add_system_paths(command: &mut Command) {
    for path in ["/usr", "/bin", "/lib", "/etc"] {
        if Path::new(path).exists() {
            command.args(["--ro", path]);
        }
    }
}

fn process_is_runnable(pid: i32) -> bool {
    let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return false;
    };
    !matches!(
        stat.rsplit_once(") ")
            .and_then(|(_, fields)| fields.chars().next()),
        Some('Z' | 'X')
    )
}

fn read_escape_results(path: &Path) -> Vec<(String, i32, i32, i32)> {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            Some((
                fields.next()?.to_string(),
                fields.next()?.parse().ok()?,
                fields.next()?.parse().ok()?,
                fields.next()?.parse().ok()?,
            ))
        })
        .collect()
}

#[test]
#[ignore = "helper process for sandbox_process_group_escape_is_denied"]
fn sandbox_escape_probe_attempt() {
    if env::var_os("OPENAGENTS_ESCAPE_PROBE").as_deref() != Some("attempt".as_ref()) {
        return;
    }
    let syscall = env::var("OPENAGENTS_ESCAPE_SYSCALL").unwrap();
    unsafe {
        *libc::__errno_location() = 0;
    }
    let result = unsafe {
        match syscall.as_str() {
            "setsid" => libc::setsid(),
            "setpgid" => libc::setpgid(0, 0),
            _ => unreachable!(),
        }
    };
    let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
    let mut output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(env::var_os("OPENAGENTS_ESCAPE_RESULTS").unwrap())
        .unwrap();
    writeln!(output, "{syscall} {} {result} {errno}", std::process::id()).unwrap();
    output.flush().unwrap();
    thread::sleep(Duration::from_secs(30));
}

#[test]
#[ignore = "helper process for sandbox_process_group_escape_is_denied"]
fn sandbox_escape_probe_supervisor() {
    if env::var_os("OPENAGENTS_ESCAPE_PROBE").as_deref() != Some("supervisor".as_ref()) {
        return;
    }
    let executable = env::current_exe().unwrap();
    let mut children = ["setsid", "setpgid"].map(|syscall| {
        Command::new(&executable)
            .args([
                "--ignored",
                "--exact",
                "sandbox_escape_probe_attempt",
                "--nocapture",
            ])
            .env("OPENAGENTS_ESCAPE_PROBE", "attempt")
            .env("OPENAGENTS_ESCAPE_SYSCALL", syscall)
            .spawn()
            .unwrap()
    });
    for child in &mut children {
        let _ = child.wait();
    }
}

#[test]
fn sandbox_process_group_escape_is_denied() {
    let temp = tempfile::tempdir().unwrap();
    let results = temp.path().join("escape-results");
    let executable = env::current_exe().unwrap();

    let mut command = Command::new(sandbox());
    add_system_paths(&mut command);
    command
        .arg("--ro")
        .arg(executable.parent().unwrap())
        .arg("--rw")
        .arg(temp.path())
        .arg("--")
        .arg(&executable)
        .args([
            "--ignored",
            "--exact",
            "sandbox_escape_probe_supervisor",
            "--nocapture",
        ])
        .env_clear()
        .env("OPENAGENTS_ESCAPE_PROBE", "supervisor")
        .env("OPENAGENTS_ESCAPE_RESULTS", &results)
        .process_group(0)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut child = command.spawn().unwrap();
    let process_group = i32::try_from(child.id()).unwrap();

    let deadline = Instant::now() + Duration::from_secs(5);
    let attempts = loop {
        let attempts = read_escape_results(&results);
        if attempts.len() == 2 {
            break attempts;
        }
        if Instant::now() >= deadline {
            unsafe {
                libc::kill(-process_group, libc::SIGKILL);
            }
            let _ = child.wait();
            panic!("sandbox escape probes did not start: {attempts:?}");
        }
        thread::sleep(Duration::from_millis(25));
    };

    unsafe {
        libc::kill(-process_group, libc::SIGKILL);
    }
    let _ = child.wait();
    thread::sleep(Duration::from_millis(250));
    let survivors = attempts
        .iter()
        .filter(|(_, pid, _, _)| process_is_runnable(*pid))
        .map(|(syscall, _, _, _)| syscall.clone())
        .collect::<Vec<_>>();
    for (_, pid, _, _) in &attempts {
        unsafe {
            libc::kill(*pid, libc::SIGKILL);
        }
    }

    assert_eq!(attempts.len(), 2);
    let setsid = attempts
        .iter()
        .find(|(syscall, _, _, _)| syscall == "setsid")
        .expect("setsid probe result missing");
    let setpgid = attempts
        .iter()
        .find(|(syscall, _, _, _)| syscall == "setpgid")
        .expect("setpgid probe result missing");
    assert_eq!(setsid.2, -1, "setsid escaped the original process group");
    assert_eq!(
        setsid.3,
        libc::EPERM,
        "setsid did not fail with errno EPERM"
    );
    assert_eq!(setpgid.2, -1, "setpgid escaped the original process group");
    assert_eq!(
        setpgid.3,
        libc::EPERM,
        "setpgid did not fail with errno EPERM"
    );
    assert!(
        survivors.is_empty(),
        "descendants survived process-group cleanup: {survivors:?}"
    );
}

#[test]
fn sandbox_allows_candidate_edits_and_denies_control_dependency_and_siblings() {
    let temp = tempfile::tempdir().unwrap();
    let workspace = temp.path().join("candidate");
    let git_dir = temp.path().join("candidate-control.git");
    let dependency = temp.path().join("dependency");
    let source_cache = temp.path().join("source-cache");
    let sibling = temp.path().join("sibling-tenant");
    let secret = temp.path().join("control-plane.secret");
    for path in [&workspace, &dependency, &source_cache, &sibling] {
        fs::create_dir(path).unwrap();
    }
    fs::write(dependency.join("dependency.txt"), "dependency\n").unwrap();
    fs::write(source_cache.join("cache.txt"), "cache\n").unwrap();
    fs::write(sibling.join("sibling.txt"), "sibling\n").unwrap();
    fs::write(&secret, "control-plane-credential\n").unwrap();
    assert!(Command::new("git")
        .args(["init", "--bare", "--quiet"])
        .arg(&git_dir)
        .status()
        .unwrap()
        .success());
    fs::write(
        workspace.join(".git"),
        format!("gitdir: {}\n", git_dir.display()),
    )
    .unwrap();

    let mut command = Command::new(sandbox());
    add_system_paths(&mut command);
    let status = command
        .arg("--ro")
        .arg(&git_dir)
        .arg("--ro")
        .arg(&dependency)
        .arg("--ro")
        .arg(&source_cache)
        .args(["--rw", "/dev"])
        .arg("--rw")
        .arg(&workspace)
        .arg("--")
        .arg("/bin/sh")
        .arg("-c")
        .arg(
            "set -eu; \
             printf allowed > \"$1/allowed.txt\"; \
             test \"$(cat \"$3/dependency.txt\")\" = dependency; \
             ! printf attack > \"$2/config\"; \
             ! git -C \"$1\" config filter.attack.clean /bin/true; \
             ! printf attack > \"$3/dependency.txt\"; \
             ! printf attack > \"$4/cache.txt\"; \
             ! cat \"$5/sibling.txt\"; \
             ! cat \"$6\"",
        )
        .arg("boundary-test")
        .arg(&workspace)
        .arg(&git_dir)
        .arg(&dependency)
        .arg(&source_cache)
        .arg(&sibling)
        .arg(&secret)
        .env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", &workspace)
        .status()
        .unwrap();

    assert!(status.success());
    assert_eq!(
        fs::read_to_string(workspace.join("allowed.txt")).unwrap(),
        "allowed"
    );
    assert_eq!(
        fs::read_to_string(dependency.join("dependency.txt")).unwrap(),
        "dependency\n"
    );
    assert_eq!(
        fs::read_to_string(source_cache.join("cache.txt")).unwrap(),
        "cache\n"
    );
}

#[test]
fn sandbox_initialization_failure_is_fail_closed() {
    let missing = tempfile::tempdir().unwrap().path().join("missing");
    let output = Command::new(sandbox())
        .arg("--ro")
        .arg(missing)
        .args(["--", "/bin/sh", "-c", "exit 0"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(125));
    assert!(String::from_utf8_lossy(&output.stderr).contains("OPENAGENTS_SANDBOX_INIT_FAILED:"));
}

#[test]
fn qa_shell_succeeds_for_allowed_paths_and_keeps_dependencies_read_only() {
    let temp = tempfile::tempdir().unwrap();
    let workspace = temp.path().join("candidate");
    let qa_home = temp.path().join("qa-home");
    let qa_tmp = temp.path().join("qa-tmp");
    let dependency = temp.path().join("dependency");
    for path in [&workspace, &qa_home, &qa_tmp, &dependency] {
        fs::create_dir(path).unwrap();
    }
    fs::write(dependency.join("fixture.txt"), "expected\n").unwrap();

    let mut command = Command::new(sandbox());
    add_system_paths(&mut command);
    let status = command
        .arg("--ro")
        .arg(&dependency)
        .args(["--rw", "/dev"])
        .arg("--rw")
        .arg(&workspace)
        .arg("--rw")
        .arg(&qa_home)
        .arg("--rw")
        .arg(&qa_tmp)
        .arg("--")
        .arg("/bin/sh")
        .arg("-lc")
        .arg(
            "set -eu; \
             test \"$(cat \"$DEPENDENCY/fixture.txt\")\" = expected; \
             printf passed > result.txt; \
             printf home > \"$HOME/qa.txt\"; \
             printf tmp > \"$TMPDIR/qa.txt\"; \
             ! printf attack > \"$DEPENDENCY/fixture.txt\"",
        )
        .current_dir(&workspace)
        .env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", &qa_home)
        .env("TMPDIR", &qa_tmp)
        .env("DEPENDENCY", &dependency)
        .status()
        .unwrap();

    assert!(status.success());
    assert_eq!(
        fs::read_to_string(workspace.join("result.txt")).unwrap(),
        "passed"
    );
    assert_eq!(
        fs::read_to_string(dependency.join("fixture.txt")).unwrap(),
        "expected\n"
    );
}
