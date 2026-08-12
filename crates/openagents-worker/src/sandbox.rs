#[cfg(target_os = "linux")]
use std::{env, os::unix::process::CommandExt, path::PathBuf};

#[cfg(target_os = "linux")]
use landlock::{
    Access, AccessFs, CompatLevel, Compatible, PathBeneath, PathFd, Ruleset, RulesetAttr,
    RulesetCreatedAttr, RulesetStatus, ABI,
};

const SANDBOX_INIT_FAILURE_EXIT_CODE: i32 = 125;

fn main() {
    if let Err(error) = dispatch() {
        eprintln!("OPENAGENTS_SANDBOX_INIT_FAILED: {error:#}");
        std::process::exit(SANDBOX_INIT_FAILURE_EXIT_CODE);
    }
}

fn dispatch() -> anyhow::Result<()> {
    #[cfg(not(target_os = "linux"))]
    anyhow::bail!("OPENAGENTS_SANDBOX_UNSUPPORTED");

    #[cfg(target_os = "linux")]
    run()
}

#[cfg(target_os = "linux")]
fn run() -> anyhow::Result<()> {
    let arguments = env::args_os().skip(1).collect::<Vec<_>>();
    if arguments.len() == 1 && arguments[0] == "--check" {
        restrict(&[PathBuf::from("/")], &[])?;
        return Ok(());
    }

    let mut read_only = Vec::new();
    let mut read_write = Vec::new();
    let mut index = 0;
    while index < arguments.len() {
        match arguments[index].to_str() {
            Some("--ro") | Some("--rw") => {
                let writable = arguments[index] == "--rw";
                index += 1;
                let path = arguments
                    .get(index)
                    .ok_or_else(|| anyhow::anyhow!("OPENAGENTS_SANDBOX_PATH_REQUIRED"))?;
                if writable {
                    read_write.push(PathBuf::from(path));
                } else {
                    read_only.push(PathBuf::from(path));
                }
                index += 1;
            }
            Some("--") => {
                index += 1;
                break;
            }
            _ => anyhow::bail!("OPENAGENTS_SANDBOX_ARGUMENT_INVALID"),
        }
    }
    let command = arguments
        .get(index)
        .ok_or_else(|| anyhow::anyhow!("OPENAGENTS_SANDBOX_COMMAND_REQUIRED"))?;
    let command_arguments = &arguments[index + 1..];
    restrict(&read_only, &read_write)?;
    Err(std::process::Command::new(command)
        .args(command_arguments)
        .exec()
        .into())
}

#[cfg(target_os = "linux")]
fn restrict(read_only: &[PathBuf], read_write: &[PathBuf]) -> anyhow::Result<()> {
    let abi = ABI::V4;
    let read_access = AccessFs::from_read(abi);
    let all_access = AccessFs::from_all(abi);
    let status =
        Ruleset::default()
            .handle_access(all_access)?
            .create()?
            .add_rules(read_only.iter().map(|path| {
                Ok::<_, anyhow::Error>(PathBeneath::new(PathFd::new(path)?, read_access))
            }))?
            .add_rules(read_write.iter().map(|path| {
                Ok::<_, anyhow::Error>(PathBeneath::new(PathFd::new(path)?, all_access))
            }))?
            .set_compatibility(CompatLevel::HardRequirement)
            .restrict_self()?;
    if status.ruleset == RulesetStatus::NotEnforced {
        anyhow::bail!("OPENAGENTS_SANDBOX_NOT_ENFORCED");
    }
    deny_process_group_escape()?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn deny_process_group_escape() -> anyhow::Result<()> {
    const BPF_LOAD_SYSCALL_FIELD: u16 = 0x20;
    const BPF_JUMP_EQUAL: u16 = 0x15;
    const BPF_RETURN: u16 = 0x06;
    const SECCOMP_DATA_ARCH_OFFSET: u32 = 4;
    const SECCOMP_DATA_SYSCALL_OFFSET: u32 = 0;
    const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
    const SECCOMP_RET_ERRNO: u32 = 0x0005_0000;
    const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;

    let mut filter = vec![
        bpf_statement(BPF_LOAD_SYSCALL_FIELD, SECCOMP_DATA_ARCH_OFFSET),
        bpf_jump(BPF_JUMP_EQUAL, native_audit_arch()?, 1, 0),
        bpf_statement(BPF_RETURN, SECCOMP_RET_KILL_PROCESS),
        bpf_statement(BPF_LOAD_SYSCALL_FIELD, SECCOMP_DATA_SYSCALL_OFFSET),
    ];
    #[cfg(target_arch = "x86_64")]
    {
        const BPF_JUMP_BITS_SET: u16 = 0x45;
        const X32_SYSCALL_BIT: u32 = 0x4000_0000;
        filter.push(bpf_jump(BPF_JUMP_BITS_SET, X32_SYSCALL_BIT, 0, 1));
        filter.push(bpf_statement(BPF_RETURN, SECCOMP_RET_KILL_PROCESS));
    }
    let denied = SECCOMP_RET_ERRNO | u32::try_from(libc::EPERM)?;
    for syscall in [libc::SYS_setsid, libc::SYS_setpgid] {
        filter.push(bpf_jump(BPF_JUMP_EQUAL, u32::try_from(syscall)?, 0, 1));
        filter.push(bpf_statement(BPF_RETURN, denied));
    }
    filter.push(bpf_statement(BPF_RETURN, SECCOMP_RET_ALLOW));
    let mut program = libc::sock_fprog {
        len: u16::try_from(filter.len())?,
        filter: filter.as_mut_ptr(),
    };

    if unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0 {
        return Err(anyhow::Error::from(std::io::Error::last_os_error())
            .context("OPENAGENTS_SECCOMP_NO_NEW_PRIVS_FAILED"));
    }
    if unsafe {
        libc::prctl(
            libc::PR_SET_SECCOMP,
            libc::SECCOMP_MODE_FILTER,
            &mut program as *mut libc::sock_fprog,
        )
    } != 0
    {
        return Err(anyhow::Error::from(std::io::Error::last_os_error())
            .context("OPENAGENTS_SECCOMP_INSTALL_FAILED"));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn bpf_statement(code: u16, value: u32) -> libc::sock_filter {
    libc::sock_filter {
        code,
        jt: 0,
        jf: 0,
        k: value,
    }
}

#[cfg(target_os = "linux")]
fn bpf_jump(code: u16, value: u32, jump_true: u8, jump_false: u8) -> libc::sock_filter {
    libc::sock_filter {
        code,
        jt: jump_true,
        jf: jump_false,
        k: value,
    }
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn native_audit_arch() -> anyhow::Result<u32> {
    Ok(0xc000_003e)
}

#[cfg(all(target_os = "linux", target_arch = "aarch64"))]
fn native_audit_arch() -> anyhow::Result<u32> {
    Ok(0xc000_00b7)
}

#[cfg(all(
    target_os = "linux",
    not(any(target_arch = "x86_64", target_arch = "aarch64"))
))]
fn native_audit_arch() -> anyhow::Result<u32> {
    anyhow::bail!("OPENAGENTS_SECCOMP_ARCH_UNSUPPORTED")
}
