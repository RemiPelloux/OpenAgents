use std::{
    net::{IpAddr, SocketAddr},
    path::{Component, Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use anyhow::Context;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use contract_core::{GitEvidence, TestEvidence, WorkerArtifact, WorkerJob, WorkerResult};
use reqwest::{header::LOCATION, redirect::Policy};
use scraper::{Html, Selector};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::{
    fs,
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    time::{sleep, timeout},
};
use uuid::Uuid;

use crate::{
    config::Config,
    model::{RunStatus, RunStore},
};

const SKILL_AUTHOR_LLM_TIMEOUT: Duration = Duration::from_secs(120);
const SKILL_AUTHOR_WORKFLOW_TIMEOUT: Duration = Duration::from_secs(285);
const MAX_RESEARCH_BODY_BYTES: usize = 2_000_000;
const SKILL_AUTHOR_TRANSPORT_ATTEMPTS: usize = 2;
const SKILL_AUTHOR_CANDIDATE_ATTEMPTS: usize = 3;
const MAX_RESEARCH_SOURCES: usize = 8;

pub async fn runtime_healthy(config: &Config) -> bool {
    command_ok(Command::new("git").arg("--version")).await
        && command_ok(Command::new(&config.opencode_binary).arg("--version")).await
        && fs::metadata(&config.shell_binary)
            .await
            .is_ok_and(|metadata| metadata.is_file())
        && fs::create_dir_all(&config.managed_root).await.is_ok()
}

async fn command_ok(command: &mut Command) -> bool {
    command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .is_ok_and(|status| status.success())
}

pub async fn execute(
    job: &WorkerJob,
    run_id: Uuid,
    config: &Config,
    store: &RunStore,
) -> anyhow::Result<WorkerResult> {
    validate_job(job)?;
    if job.job_type == "agent.skill_author" {
        return timeout(
            SKILL_AUTHOR_WORKFLOW_TIMEOUT,
            author_skill(job, run_id, config, store),
        )
        .await
        .map_err(|_| anyhow::anyhow!("SKILL_AUTHOR_WORKFLOW_TIMEOUT"))?;
    }
    let inputs = job.inputs.get("inputs").unwrap_or(&job.inputs);
    let repository = canonical_repository(inputs, &config.allowed_repositories).await?;
    let base_ref = string(inputs, "base_ref")?;
    let tests = string_array(inputs, "test_commands")?;
    let prompt = string(inputs, "prompt").unwrap_or_else(|_| {
        job.inputs
            .get("task")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    });
    let prompt = engineering_prompt(&prompt);
    if contains_secret(&serde_json::to_string(inputs)?) {
        anyhow::bail!("INPUT_SECRET_REJECTED");
    }
    let (workspace, base_sha) = create_worktree(
        &repository,
        &config.managed_root,
        job.ticket_id,
        run_id,
        &base_ref,
    )
    .await?;
    ensure_not_cancelled(store, run_id).await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "worktree.created",
            json!({"worktree":workspace,"repository":repository}),
        )
        .await;
    let engine = run_opencode(config, job, run_id, &workspace, &prompt, store).await?;
    if let Some(kind) = detect_secret(&engine.stdout).or_else(|| detect_secret(&engine.stderr)) {
        tracing::warn!(
            run_id = %run_id,
            secret_kind = kind.label(),
            "OpenCode output rejected by credential guard"
        );
        anyhow::bail!("ENGINE_SECRET_LEAK_REJECTED");
    }
    ensure_not_cancelled(store, run_id).await?;
    let test_evidence = run_tests(&workspace, &tests, run_id, store).await?;
    if engine.exit_status != 0 || test_evidence.iter().any(|test| test.exit_status != 0) {
        anyhow::bail!("engine or declared tests failed");
    }
    let git = commit_changes(
        &workspace,
        &repository,
        &base_sha,
        job.ticket_id,
        run_id,
        config.git_sign_commits,
    )
    .await?;
    let changed_files = changed_files_artifact(&workspace, &base_sha, &git.commit_sha)?;
    let artifact = persist_events(&config.managed_root, run_id, &engine.stdout).await?;
    Ok(WorkerResult {
        run_id,
        artifacts: vec![artifact, changed_files],
        stderr: nonempty(sanitize(&engine.stderr)),
        exit_status: engine.exit_status,
        tests: test_evidence,
        git: Some(git),
        engine_session_id: engine.session_id,
    })
}

fn engineering_prompt(task: &str) -> String {
    format!(
        "{task}\n\nOpenOS worktree isolation contract:\n\
         - The current working directory is the only permitted file-editing and Git target.\n\
         - Treat repository paths from the task as source identity metadata, never as an edit location.\n\
         - Resolve requested paths relative to the current working directory; never edit an absolute repository path or use git -C outside the current working directory.\n\
         - Fail explicitly if a requested target resolves outside the current working directory.\n\n\
         OpenOS generalization contract:\n\
         - Identify facts that vary between valid instances and expose them through existing configuration, function arguments, typed schemas, or discovery outputs.\n\
         - Keep the triggering app name, owner, repository, URL, credential reference, and destination as instance data, not reusable control flow.\n\
         - When a value is unknown, discover it from available evidence and registered tools; fail explicitly when evidence, authorization, or a required real tool is unavailable.\n\
         - Implement the smallest reusable boundary justified by at least two concrete instances; avoid speculative abstractions.\n\
         - Add behavior tests for the triggering instance and a materially different instance whenever the changed behavior is reusable."
    )
}

fn changed_files_artifact(
    workspace: &Path,
    base_sha: &str,
    commit_sha: &str,
) -> anyhow::Result<WorkerArtifact> {
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(workspace)
        .args(["diff", "--name-only", base_sha, commit_sha])
        .output()?;
    if !output.status.success() {
        anyhow::bail!("CHANGED_FILES_EVIDENCE_FAILED");
    }
    let files: Vec<&str> = std::str::from_utf8(&output.stdout)?
        .lines()
        .filter(|value| !value.trim().is_empty())
        .collect();
    if files.is_empty() {
        anyhow::bail!("CHANGED_FILES_EVIDENCE_EMPTY");
    }
    Ok(WorkerArtifact {
        kind: "changed_files".into(),
        name: "Changed files".into(),
        uri: format!("git://{commit_sha}/changed-files"),
        sha256: Some(format!("{:x}", Sha256::digest(&output.stdout))),
        metadata: json!({"base_sha":base_sha,"commit_sha":commit_sha,"files":files}),
    })
}

fn validate_job(job: &WorkerJob) -> anyhow::Result<()> {
    let supported = (job.job_type == "engineering.opencode"
        && job
            .required_capabilities
            .iter()
            .any(|value| value == "invoke_opencode"))
        || (job.job_type == "agent.skill_author"
            && ["skill_author", "web_search", "web_extract"]
                .iter()
                .all(|required| {
                    job.required_capabilities
                        .iter()
                        .any(|value| value == required)
                }));
    if !supported {
        anyhow::bail!("UNSUPPORTED_JOB_TYPE_OR_CAPABILITY");
    }
    if job
        .deadline
        .is_some_and(|deadline| deadline <= chrono::Utc::now())
    {
        anyhow::bail!("JOB_DEADLINE_EXPIRED");
    }
    Ok(())
}

#[derive(Debug)]
struct ResearchSource {
    url: String,
    title: String,
    text: String,
}

struct ValidatedResearchUrl {
    url: url::Url,
    host: String,
    addresses: Vec<SocketAddr>,
    pin_dns: bool,
}

async fn author_skill(
    job: &WorkerJob,
    run_id: Uuid,
    config: &Config,
    store: &RunStore,
) -> anyhow::Result<WorkerResult> {
    let input = job.inputs.get("inputs").unwrap_or(&job.inputs);
    let slug = string(input, "proposed_skill_id")?;
    let query = string(input, "research_query")?;
    let criteria = string_array(input, "success_criteria")?;
    let generalization = input
        .get("generalization")
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_REQUIRED"))?;
    validate_generalization_contract(&generalization)?;
    let authoring_criteria = authoring_success_criteria(&criteria);
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.search.started",
            json!({"query":query,"tool":"web_search"}),
        )
        .await;
    let search_http = reqwest::Client::builder()
        .user_agent("OpenAgents skill_author/0.1")
        .timeout(config.request_timeout)
        .build()?;
    let mut candidates = web_search(&search_http, &query).await?;
    rank_search_candidates(&mut candidates, &query);
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.search.completed",
            json!({"tool":"web_search","result_count":candidates.len()}),
        )
        .await;
    let mut sources: Vec<ResearchSource> = Vec::new();
    let mut rejected_sources = Vec::new();
    extract_research_candidates(
        config.request_timeout,
        candidates,
        &mut sources,
        &mut rejected_sources,
    )
    .await;
    let mut hosts = research_hosts(&sources);
    if hosts.len() < 2 {
        let diversified_query = diversify_research_query(&query, &hosts);
        let mut diversified_candidates = web_search(&search_http, &diversified_query)
            .await
            .unwrap_or_default();
        rank_search_candidates(&mut diversified_candidates, &query);
        store
            .update(
                run_id,
                RunStatus::Running,
                "research.search.diversified",
                json!({
                    "tool":"web_search",
                    "query":diversified_query,
                    "excluded_hosts":hosts,
                    "result_count":diversified_candidates.len(),
                }),
            )
            .await;
        extract_research_candidates(
            config.request_timeout,
            diversified_candidates,
            &mut sources,
            &mut rejected_sources,
        )
        .await;
        hosts = research_hosts(&sources);
    }
    if sources.len() < 2 || hosts.len() < 2 {
        store
            .update(
                run_id,
                RunStatus::Running,
                "research.extract.insufficient",
                json!({
                    "tool":"web_extract",
                    "source_count":sources.len(),
                    "independent_hosts":hosts.len(),
                    "rejected":rejected_sources.into_iter().take(8).collect::<Vec<_>>(),
                }),
            )
            .await;
        anyhow::bail!("AUTHORITATIVE_RESEARCH_INSUFFICIENT: fewer than two independent documents were extracted");
    }
    store
        .update(
            run_id,
            RunStatus::Running,
            "research.extract.completed",
            json!({
                "tool":"web_extract",
                "source_count":sources.len(),
                "source_hosts":sources.iter().filter_map(|source| url::Url::parse(&source.url).ok()).filter_map(|value| value.host_str().map(str::to_owned)).collect::<std::collections::HashSet<_>>(),
            }),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "authoring.started",
            json!({"profile":"skill_author","source_count":sources.len()}),
        )
        .await;
    let (_authored, description, body, source_meta, validation) = author_validated_skill(
        config,
        &slug,
        &query,
        &authoring_criteria,
        &generalization,
        &sources,
        run_id,
        store,
    )
    .await?;
    store
        .update(
            run_id,
            RunStatus::Running,
            "authoring.completed",
            json!({"profile":"skill_author","body_length":body.len()}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "validation.started",
            json!({"criteria_count":authoring_criteria.len(),"workflow_criteria_count":criteria.len()-authoring_criteria.len()}),
        )
        .await;
    store
        .update(
            run_id,
            RunStatus::Running,
            "validation.completed",
            json!({"passed":true,"criteria_count":authoring_criteria.len()}),
        )
        .await;
    let skill = json!({
        "slug":slug,"description":description,"skill_md":body,"sources":source_meta,
        "generalization":generalization,
        "validation":validation,
        "provenance":{"research_tools":["web_search","web_extract"],"research_query":query,"provider":"openai-compatible","model":config.llm_model,"source_count":source_meta.len()}
    });
    let serialized = serde_json::to_vec(&skill)?;
    let sha256 = format!("{:x}", Sha256::digest(&serialized));
    Ok(WorkerResult {
        run_id,
        artifacts: vec![WorkerArtifact {
            kind: "organization_skill".into(),
            name: format!("{slug}.json"),
            uri: format!("openagents://runs/{run_id}/skills/{slug}"),
            sha256: Some(sha256),
            metadata: json!({"skill":skill}),
        }],
        stderr: None,
        exit_status: 0,
        tests: vec![TestEvidence {
            command: "skill_author_validation".into(),
            exit_status: 0,
            passed: authoring_criteria.len() as u32 + 6,
            failed: 0,
            output_uri: None,
        }],
        git: None,
        engine_session_id: Some(format!("skill_author:{run_id}")),
    })
}

async fn author_validated_skill(
    config: &Config,
    slug: &str,
    query: &str,
    criteria: &[String],
    generalization: &Value,
    sources: &[ResearchSource],
    run_id: Uuid,
    store: &RunStore,
) -> anyhow::Result<(Value, String, String, Vec<Value>, Value)> {
    let mut feedback: Option<String> = None;
    for attempt in 0..SKILL_AUTHOR_CANDIDATE_ATTEMPTS {
        let authored = author_with_llm(
            config,
            slug,
            query,
            criteria,
            generalization,
            sources,
            feedback.as_deref(),
        )
        .await?;
        let description = authored
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        let body = authored
            .get("skill_md")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_string();
        let result = if description.len() < 20 || body.len() < 120 || body.len() > 18_000 {
            Err(anyhow::anyhow!("SKILL_AUTHOR_OUTPUT_INVALID"))
        } else {
            select_authoritative_sources(&authored, sources).and_then(|source_meta| {
                validate_authored_skill(
                    &authored,
                    criteria,
                    generalization,
                    &body,
                    &source_meta,
                    sources,
                )
                .map(|validation| {
                    (
                        authored.clone(),
                        description.clone(),
                        body.clone(),
                        source_meta,
                        validation,
                    )
                })
            })
        };
        match result {
            Ok(candidate) => return Ok(candidate),
            Err(error) if attempt + 1 < SKILL_AUTHOR_CANDIDATE_ATTEMPTS => {
                feedback = Some(error.to_string());
                store.update(run_id, RunStatus::Running, "authoring.repairing", json!({
                    "profile":"skill_author","attempt":attempt + 2,"validation_error":error.to_string()
                })).await;
            }
            Err(error) => return Err(error),
        }
    }
    unreachable!("bounded authoring attempts always return")
}

async fn extract_research_candidates(
    request_timeout: Duration,
    candidates: Vec<(String, String)>,
    sources: &mut Vec<ResearchSource>,
    rejected_sources: &mut Vec<Value>,
) {
    for (url, title) in candidates.into_iter().take(24) {
        let (resolved_url, text) = match web_extract(request_timeout, &url).await {
            Ok(value) => value,
            Err(error) => {
                rejected_sources.push(json!({
                    "host":url::Url::parse(&url).ok().and_then(|value| value.host_str().map(str::to_owned)),
                    "error":error.to_string().chars().take(240).collect::<String>(),
                }));
                continue;
            }
        };
        if sources.iter().any(|source| source.url == resolved_url) {
            continue;
        }
        let resolved_host = url::Url::parse(&resolved_url)
            .ok()
            .and_then(|value| value.host_str().map(str::to_owned));
        let same_host_count = resolved_host.as_ref().map_or(0, |host| {
            sources
                .iter()
                .filter(|source| source_host(source).as_ref() == Some(host))
                .count()
        });
        if same_host_count >= 2 {
            continue;
        }
        sources.push(ResearchSource {
            url: resolved_url,
            title,
            text,
        });
        if sources.len() >= MAX_RESEARCH_SOURCES {
            break;
        }
    }
}

fn source_host(source: &ResearchSource) -> Option<String> {
    url::Url::parse(&source.url)
        .ok()
        .and_then(|value| value.host_str().map(str::to_owned))
}

fn research_hosts(sources: &[ResearchSource]) -> std::collections::HashSet<String> {
    sources.iter().filter_map(source_host).collect()
}

fn authoring_success_criteria(criteria: &[String]) -> Vec<String> {
    criteria
        .iter()
        .filter(|criterion| {
            let lower = criterion.to_ascii_lowercase();
            !lower.contains("activated privately")
                && !(lower.contains("activate")
                    && lower.contains("skill")
                    && lower.contains("privately"))
                && !lower.contains("after explicit user confirmation")
                && !(lower.contains("confirmation")
                    && (lower.contains("author") || lower.contains("activat")))
                && !lower.contains("retries the originating")
                && !lower.contains("after successful activation")
        })
        .cloned()
        .collect()
}

fn diversify_research_query(query: &str, hosts: &std::collections::HashSet<String>) -> String {
    let exclusions = hosts
        .iter()
        .take(3)
        .map(|host| format!("-site:{host}"))
        .collect::<Vec<_>>()
        .join(" ");
    format!("{query} official primary source {exclusions}")
}

async fn web_search(http: &reqwest::Client, query: &str) -> anyhow::Result<Vec<(String, String)>> {
    let mut results = Vec::new();
    for search_query in search_query_variants(query) {
        let encoded = urlencoding::encode(&search_query);
        for (endpoint, selector) in [
            (
                format!("https://search.brave.com/search?q={encoded}&source=web"),
                "div.snippet[data-type=\"web\"] a[href]",
            ),
            (
                format!("https://html.duckduckgo.com/html/?q={encoded}"),
                "a.result__a",
            ),
            (
                format!("https://lite.duckduckgo.com/lite/?q={encoded}"),
                "a.result-link",
            ),
            (
                format!("https://www.bing.com/search?q={encoded}"),
                "li.b_algo h2 a",
            ),
        ] {
            let Ok(response) = http.get(endpoint).send().await else {
                continue;
            };
            let Ok(response) = response.error_for_status() else {
                continue;
            };
            let Ok(body) = response.text().await else {
                continue;
            };
            append_search_results(&body, selector, &mut results)?;
        }
    }
    if results.len() < 2 {
        anyhow::bail!("WEB_SEARCH_RETURNED_INSUFFICIENT_RESULTS");
    }
    Ok(results)
}

fn search_query_variants(query: &str) -> Vec<String> {
    const STOP_WORDS: &[&str] = &[
        "using",
        "only",
        "research",
        "current",
        "from",
        "with",
        "into",
        "including",
        "include",
        "applicable",
        "least",
        "sources",
        "source",
        "capture",
        "record",
        "identify",
        "reconcile",
        "such",
        "that",
        "this",
        "then",
        "than",
        "their",
    ];
    let mut seen = std::collections::HashSet::new();
    let concise = query
        .split_whitespace()
        .map(|word| {
            word.trim_matches(|character: char| {
                !character.is_ascii_alphanumeric() && character != '.' && character != '-'
            })
        })
        .filter(|word| word.len() >= 3)
        .filter(|word| !STOP_WORDS.contains(&word.to_ascii_lowercase().as_str()))
        .filter(|word| seen.insert(word.to_ascii_lowercase()))
        .take(20)
        .collect::<Vec<_>>()
        .join(" ");
    if concise.is_empty() || concise == query {
        vec![query.to_string()]
    } else {
        vec![concise, query.to_string()]
    }
}

fn rank_search_candidates(candidates: &mut [(String, String)], query: &str) {
    candidates.sort_by_cached_key(|(url, title)| {
        std::cmp::Reverse(search_candidate_score(url, title, query))
    });
}

fn search_candidate_score(url: &str, title: &str, query: &str) -> usize {
    let searchable = format!("{url} {title}").to_ascii_lowercase();
    let query_terms = search_query_variants(query)
        .into_iter()
        .next()
        .unwrap_or_default()
        .split_whitespace()
        .map(str::to_ascii_lowercase)
        .filter(|term| term.len() >= 4)
        .collect::<std::collections::HashSet<_>>();
    let term_score = query_terms
        .iter()
        .filter(|term| searchable.contains(term.as_str()))
        .count()
        * 4;
    let authority_score = [
        ("docs.", 10),
        ("github.com/", 7),
        ("official", 6),
        ("standard", 5),
        ("specification", 5),
        ("schematron", 5),
        ("/rules", 4),
        ("/schema", 4),
        (".gov/", 8),
        (".eu/", 6),
        (".org/", 4),
    ]
    .iter()
    .filter_map(|(needle, score)| searchable.contains(needle).then_some(score))
    .sum::<usize>();
    term_score + authority_score
}

fn append_search_results(
    body: &str,
    selector: &str,
    results: &mut Vec<(String, String)>,
) -> anyhow::Result<()> {
    let document = Html::parse_document(body);
    let selector = Selector::parse(selector).map_err(|error| anyhow::anyhow!(error.to_string()))?;
    for link in document.select(&selector) {
        let Some(raw) = link.value().attr("href") else {
            continue;
        };
        let Some(url) = normalize_search_result_url(raw) else {
            continue;
        };
        let title = link.text().collect::<Vec<_>>().join(" ").trim().to_string();
        if !results.iter().any(|(existing, _)| existing == &url) {
            results.push((url, title));
        }
    }
    Ok(())
}

fn normalize_search_result_url(raw: &str) -> Option<String> {
    let absolute = if raw.starts_with("//") {
        format!("https:{raw}")
    } else if raw.starts_with('/') {
        format!("https://duckduckgo.com{raw}")
    } else {
        raw.to_string()
    };
    let parsed = url::Url::parse(&absolute).ok()?;
    let host = parsed.host_str()?.to_ascii_lowercase();
    let target = if let Some((_, value)) = parsed.query_pairs().find(|(key, _)| key == "uddg") {
        value.into_owned()
    } else if (host == "bing.com" || host.ends_with(".bing.com"))
        && parsed.path().starts_with("/ck/a")
    {
        let encoded = parsed
            .query_pairs()
            .find(|(key, _)| key == "u")?
            .1
            .into_owned();
        let encoded = encoded.strip_prefix("a1").unwrap_or(&encoded);
        String::from_utf8(URL_SAFE_NO_PAD.decode(encoded).ok()?).ok()?
    } else {
        absolute
    };
    let target = url::Url::parse(&target).ok()?;
    let target_host = target.host_str()?.to_ascii_lowercase();
    if target.scheme() != "https" || is_search_engine_host(&target_host) {
        return None;
    }
    Some(target.to_string())
}

fn is_search_engine_host(host: &str) -> bool {
    ["bing.com", "duckduckgo.com", "search.brave.com"]
        .iter()
        .any(|search_host| host == *search_host || host.ends_with(&format!(".{search_host}")))
}

async fn web_extract(request_timeout: Duration, url: &str) -> anyhow::Result<(String, String)> {
    let mut current = validate_public_https_url(url).await?;
    let mut redirects = 0;
    let response = loop {
        let mut builder = reqwest::Client::builder()
            .user_agent("OpenAgents skill_author/0.1")
            .timeout(request_timeout)
            .redirect(Policy::none());
        if current.pin_dns {
            builder = builder.resolve_to_addrs(&current.host, &current.addresses);
        }
        let response = builder.build()?.get(current.url.clone()).send().await?;
        if !response.status().is_redirection() {
            break response.error_for_status()?;
        }
        redirects += 1;
        if redirects > 5 {
            anyhow::bail!("too many research source redirects");
        }
        let location = response
            .headers()
            .get(LOCATION)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| anyhow::anyhow!("redirect missing location"))?;
        current = validate_public_https_url(current.url.join(location)?.as_str()).await?;
    };
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESEARCH_BODY_BYTES as u64)
    {
        anyhow::bail!("research document exceeds size limit");
    }
    let mut response = response;
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if bytes.len() + chunk.len() > MAX_RESEARCH_BODY_BYTES {
            anyhow::bail!("research document exceeds size limit");
        }
        bytes.extend_from_slice(&chunk);
    }
    if bytes.len() < 200 {
        anyhow::bail!("document too short");
    }
    let raw = String::from_utf8_lossy(&bytes);
    let document = Html::parse_document(&raw);
    let selector = Selector::parse("body").map_err(|error| anyhow::anyhow!(error.to_string()))?;
    let text = document
        .select(&selector)
        .flat_map(|node| node.text())
        .collect::<Vec<_>>()
        .join(" ");
    let normalized = if text.trim().len() >= 200 {
        text
    } else {
        raw.into_owned()
    };
    Ok((
        current.url.to_string(),
        normalized.chars().take(12_000).collect(),
    ))
}

async fn validate_public_https_url(raw: &str) -> anyhow::Result<ValidatedResearchUrl> {
    let parsed = url::Url::parse(raw)?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port_or_known_default() != Some(443)
    {
        anyhow::bail!("research source must be credential-free HTTPS on port 443");
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow::anyhow!("research source has no host"))?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if host == "localhost"
        || host.ends_with(".localhost")
        || host.ends_with(".local")
        || host.ends_with(".internal")
    {
        anyhow::bail!("research source host is not public");
    }
    if let Ok(ip) = host.parse::<IpAddr>() {
        if !is_public_ip(ip) {
            anyhow::bail!("research source address is not public");
        }
        return Ok(ValidatedResearchUrl {
            url: parsed,
            host,
            addresses: vec![SocketAddr::new(ip, 443)],
            pin_dns: false,
        });
    }
    let resolved = tokio::net::lookup_host((host.as_str(), 443))
        .await?
        .collect::<Vec<_>>();
    if resolved.is_empty() || resolved.iter().any(|address| !is_public_ip(address.ip())) {
        anyhow::bail!("research source resolved to a non-public address");
    }
    let addresses = resolved
        .into_iter()
        .map(|address| SocketAddr::new(address.ip(), 443))
        .collect::<Vec<_>>();
    Ok(ValidatedResearchUrl {
        url: parsed,
        host,
        addresses,
        pin_dns: true,
    })
}

fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(value) => {
            !(value.is_private()
                || value.is_loopback()
                || value.is_link_local()
                || value.is_broadcast()
                || value.is_documentation()
                || value.is_unspecified()
                || value.is_multicast())
        }
        IpAddr::V6(value) => {
            let segments = value.segments();
            !(value.is_loopback()
                || value.is_unspecified()
                || value.is_multicast()
                || segments[0] & 0xfe00 == 0xfc00
                || segments[0] & 0xffc0 == 0xfe80
                || (segments[0] == 0x2001 && segments[1] == 0x0db8))
        }
    }
}

fn select_authoritative_sources(
    authored: &Value,
    extracted: &[ResearchSource],
) -> anyhow::Result<Vec<Value>> {
    let selected = authored
        .get("sources")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            anyhow::anyhow!("skill_author returned no authoritative source selection")
        })?;
    let mut hosts = std::collections::HashSet::new();
    let mut result = Vec::new();
    for source in selected {
        let url = source
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let authority = source
            .get("authority")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        let Some(extracted_source) = extracted.iter().find(|candidate| candidate.url == url) else {
            anyhow::bail!("skill_author selected a source that was not extracted");
        };
        if authority.len() < 12 {
            anyhow::bail!("skill_author did not justify source authority");
        }
        let host = url::Url::parse(url)?
            .host_str()
            .ok_or_else(|| anyhow::anyhow!("selected source has no host"))?
            .to_ascii_lowercase();
        if hosts.insert(host) {
            result.push(json!({
                "url":extracted_source.url,
                "title":extracted_source.title,
                "authority":authority,
            }));
        }
    }
    if result.len() < 2 {
        anyhow::bail!("AUTHORITATIVE_RESEARCH_INSUFFICIENT: fewer than two independently selected primary sources");
    }
    Ok(result)
}

fn generalization_field<'a>(
    generalization: &'a Value,
    snake: &str,
    camel: &str,
) -> Option<&'a Value> {
    generalization
        .get(snake)
        .or_else(|| generalization.get(camel))
}

fn generalization_scenarios(generalization: &Value) -> anyhow::Result<&Vec<Value>> {
    generalization_field(
        generalization,
        "validation_scenarios",
        "validationScenarios",
    )
    .and_then(Value::as_array)
    .filter(|scenarios| scenarios.len() >= 2)
    .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_REQUIRES_TWO_SCENARIOS"))
}

fn validate_generalization_contract(generalization: &Value) -> anyhow::Result<()> {
    let capability = generalization
        .get("capability")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| value.len() >= 12)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_CAPABILITY_INVALID"))?;
    let parameters = generalization
        .get("parameters")
        .and_then(Value::as_array)
        .filter(|values| !values.is_empty())
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETERS_REQUIRED"))?;
    let mut names = std::collections::HashSet::new();
    let mut required = Vec::new();
    for parameter in parameters {
        let name = parameter
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| {
                let mut chars = name.chars();
                chars.next().is_some_and(|value| value.is_ascii_lowercase())
                    && chars.all(|value| {
                        value.is_ascii_lowercase() || value.is_ascii_digit() || value == '_'
                    })
            })
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETER_NAME_INVALID"))?;
        if !names.insert(name) {
            anyhow::bail!("SKILL_GENERALIZATION_PARAMETER_DUPLICATE");
        }
        if parameter
            .get("schema")
            .and_then(|schema| schema.get("type"))
            .and_then(Value::as_str)
            .is_none()
        {
            anyhow::bail!("SKILL_GENERALIZATION_PARAMETER_SCHEMA_REQUIRED");
        }
        if parameter.get("required").and_then(Value::as_bool) == Some(true) {
            required.push(name);
        }
    }
    let discovery = generalization_field(generalization, "discovery_strategy", "discoveryStrategy")
        .and_then(Value::as_array)
        .filter(|values| values.len() >= 2)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_DISCOVERY_STRATEGY_REQUIRED"))?;
    if capability.is_empty()
        || discovery
            .iter()
            .any(|value| value.as_str().is_none_or(|step| step.trim().is_empty()))
    {
        anyhow::bail!("SKILL_GENERALIZATION_DISCOVERY_STRATEGY_INVALID");
    }
    let invariants = generalization
        .get("invariants")
        .and_then(Value::as_array)
        .filter(|values| !values.is_empty())
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_INVARIANTS_REQUIRED"))?;
    if invariants
        .iter()
        .any(|value| value.as_str().is_none_or(|item| item.trim().is_empty()))
    {
        anyhow::bail!("SKILL_GENERALIZATION_INVARIANTS_INVALID");
    }
    let mut scenario_bindings: Vec<&Value> = Vec::new();
    let instance_bindings =
        generalization_field(generalization, "instance_bindings", "instanceBindings")
            .and_then(Value::as_object)
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_INSTANCE_BINDINGS_REQUIRED"))?;
    if required
        .iter()
        .any(|name| !instance_bindings.contains_key(*name))
    {
        anyhow::bail!("SKILL_GENERALIZATION_INSTANCE_BINDINGS_INCOMPLETE");
    }
    for scenario in generalization_scenarios(generalization)? {
        let bindings = scenario
            .get("bindings")
            .and_then(Value::as_object)
            .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_SCENARIO_BINDINGS_REQUIRED"))?;
        if scenario
            .get("name")
            .and_then(Value::as_str)
            .is_none_or(|name| name.trim().len() < 3)
            || required.iter().any(|name| !bindings.contains_key(*name))
        {
            anyhow::bail!("SKILL_GENERALIZATION_SCENARIO_INCOMPLETE");
        }
        let bindings_value = scenario
            .get("bindings")
            .expect("bindings were validated above");
        if scenario_bindings.contains(&bindings_value) {
            anyhow::bail!("SKILL_GENERALIZATION_SCENARIOS_NOT_DISTINCT");
        }
        scenario_bindings.push(bindings_value);
    }
    Ok(())
}

fn validate_parameterized_body(generalization: &Value, body: &str) -> anyhow::Result<()> {
    let lower = body.to_ascii_lowercase();
    if !lower.contains("runtime parameter") {
        anyhow::bail!("SKILL.md must include a Runtime parameters section");
    }
    let parameters = generalization
        .get("parameters")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("SKILL_GENERALIZATION_PARAMETERS_REQUIRED"))?;
    for parameter in parameters {
        let name = parameter
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if !body.contains(&format!("`{name}`"))
            && !body.contains(&format!("{{{{{name}}}}}"))
            && !body.contains(&format!("${{{name}}}"))
        {
            anyhow::bail!("SKILL.md does not expose approved runtime parameter {name}");
        }
    }
    if let Some(bindings) =
        generalization_field(generalization, "instance_bindings", "instanceBindings")
            .and_then(Value::as_object)
    {
        for value in bindings.values().filter_map(Value::as_str) {
            if (value.starts_with("https://") || value.starts_with("http://"))
                && body.contains(value)
            {
                anyhow::bail!("SKILL.md hardcodes an instance URL instead of a runtime parameter");
            }
        }
    }
    Ok(())
}

fn validate_authored_skill(
    authored: &Value,
    success_criteria: &[String],
    generalization: &Value,
    body: &str,
    sources: &[Value],
    extracted_sources: &[ResearchSource],
) -> anyhow::Result<Value> {
    let validation = authored
        .get("validation")
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no validation evidence"))?;
    let evidence = validation
        .get("criteria")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no criterion evidence"))?;
    if validation.get("passed").and_then(Value::as_bool) != Some(true) {
        let failed = evidence
            .iter()
            .filter(|item| item.get("passed").and_then(Value::as_bool) != Some(true))
            .filter_map(|item| item.get("criterion").and_then(Value::as_str))
            .take(3)
            .collect::<Vec<_>>()
            .join("; ");
        anyhow::bail!("skill_author validation did not pass: {failed}");
    }
    let normalized_body = normalize_evidence(body);
    for criterion in success_criteria {
        let item = evidence
            .iter()
            .find(|item| item.get("criterion").and_then(Value::as_str) == Some(criterion.as_str()));
        let Some(item) = item else {
            anyhow::bail!("skill_author omitted an approved success criterion");
        };
        let evidence = item
            .get("evidence")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| value.len() >= 20)
            .ok_or_else(|| anyhow::anyhow!("skill_author supplied weak criterion evidence"))?;
        let normalized_evidence = normalize_evidence(evidence);
        let grounded_in_body = normalized_body.contains(&normalized_evidence);
        let grounded_in_selected_source = extracted_sources.iter().any(|source| {
            sources.iter().any(|selected| {
                selected.get("url").and_then(Value::as_str) == Some(source.url.as_str())
            }) && normalize_evidence(&source.text).contains(&normalized_evidence)
        });
        if item.get("passed").and_then(Value::as_bool) != Some(true)
            || (!grounded_in_body && !grounded_in_selected_source)
        {
            anyhow::bail!("skill_author supplied weak criterion evidence");
        }
    }
    for source in sources {
        let url = source
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if url.is_empty() || !body.contains(url) {
            anyhow::bail!("SKILL.md does not cite every selected authoritative source");
        }
    }
    let generality_scenarios = validation
        .get("generality_scenarios")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("skill_author returned no generality scenario evidence"))?;
    let approved_scenarios = generalization_scenarios(generalization)?;
    for approved in approved_scenarios {
        let name = approved
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let result = generality_scenarios
            .iter()
            .find(|candidate| candidate.get("name").and_then(Value::as_str) == Some(name))
            .ok_or_else(|| {
                anyhow::anyhow!("skill_author omitted an approved generality scenario")
            })?;
        if result.get("passed").and_then(Value::as_bool) != Some(true)
            || result
                .get("evidence")
                .and_then(Value::as_str)
                .is_none_or(|value| value.trim().len() < 20)
        {
            anyhow::bail!("skill_author did not pass an approved generality scenario");
        }
    }
    validate_parameterized_body(generalization, body)?;
    Ok(json!({
        "passed":true,
        "checks":[
            "structured_output","source_count","independent_hosts",
            "authority_rationales","extracted_source_membership","body_bounds",
            "approved_success_criteria","grounded_criterion_evidence","source_citations",
            "runtime_parameterization","generality_scenarios"
        ],
        "criteria":evidence,
        "generality_scenarios":generality_scenarios,
    }))
}

fn normalize_evidence(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

async fn author_with_llm(
    config: &Config,
    slug: &str,
    query: &str,
    criteria: &[String],
    generalization: &Value,
    sources: &[ResearchSource],
    validation_feedback: Option<&str>,
) -> anyhow::Result<Value> {
    let source_context = sources
        .iter()
        .map(|source| json!({
            "url":source.url,
            "host":url::Url::parse(&source.url).ok().and_then(|value| value.host_str().map(str::to_owned)),
            "title":source.title,
            "extract":source.text,
        }))
        .collect::<Vec<_>>();
    let base = config.llm_base_url.trim_end_matches('/');
    let url = if base.ends_with("/v1") {
        format!("{base}/chat/completions")
    } else {
        format!("{base}/v1/chat/completions")
    };
    let llm = reqwest::Client::builder()
        .timeout(SKILL_AUTHOR_LLM_TIMEOUT)
        .redirect(Policy::none())
        .build()?;
    let mut messages = vec![
        json!({"role":"system","content":"You are the approved OpenAgents skill_author. Write concise reusable procedural instructions grounded only in the supplied extracted sources. The approved generalization contract is authoritative: core steps must use its runtime parameter names and discovery branches, never fixed values from instance_bindings. Include a Runtime parameters section and make the procedure executable for every validation scenario. Target-specific values may appear only as clearly labeled examples, never as required constants. Select at least two primary or official sources from different supplied hosts, explain each source's authority, and cite every selected exact URL in skill_md. Evaluate every supplied success criterion verbatim. For each criterion, evidence must be an exact contiguous quotation of at least 20 characters copied from skill_md or from one of the selected supplied extracts; paraphrases are invalid. Return a passed generality_scenarios item with meaningful evidence for every approved scenario. Only set validation.passed=true when every criterion and generality scenario is satisfied. The sources array must contain exact supplied URLs from at least two distinct hosts. If the supplied sources are not authoritative enough, do not submit a skill. Do not claim activation."}),
        json!({"role":"user","content":serde_json::to_string(&json!({"approved_slug":slug,"research_query":query,"success_criteria":criteria,"generalization":generalization,"sources":source_context}))?}),
    ];
    if let Some(feedback) = validation_feedback {
        messages.push(json!({"role":"user","content":format!("The previous candidate failed strict validation: {feedback}. Repair only the candidate. Criterion evidence must be copied verbatim from skill_md or one selected extract; never cite this request as evidence.")}));
    }
    let payload = json!({
        "model":config.llm_model,"temperature":0,
        "messages":messages,
        "tools":[{"type":"function","function":{"name":"submit_skill","description":"Submit the authored skill.","parameters":{
            "type":"object","additionalProperties":false,"required":["description","skill_md","sources","validation"],
            "properties":{"description":{"type":"string","minLength":20},"skill_md":{"type":"string","minLength":120,"maxLength":18000},"sources":{"type":"array","minItems":2,"maxItems":8,"items":{"type":"object","additionalProperties":false,"required":["url","authority"],"properties":{"url":{"type":"string"},"authority":{"type":"string","minLength":12}}}},"validation":{"type":"object","additionalProperties":false,"required":["passed","criteria","generality_scenarios"],"properties":{"passed":{"type":"boolean"},"criteria":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"object","additionalProperties":false,"required":["criterion","passed","evidence"],"properties":{"criterion":{"type":"string"},"passed":{"type":"boolean"},"evidence":{"type":"string","minLength":20}}}},"generality_scenarios":{"type":"array","minItems":2,"maxItems":4,"items":{"type":"object","additionalProperties":false,"required":["name","passed","evidence"],"properties":{"name":{"type":"string"},"passed":{"type":"boolean"},"evidence":{"type":"string","minLength":20}}}}}}}
        }}}],
        "tool_choice":{"type":"function","function":{"name":"submit_skill"}}
    });
    let mut response = None;
    for attempt in 0..SKILL_AUTHOR_TRANSPORT_ATTEMPTS {
        match llm
            .post(&url)
            .bearer_auth(&config.llm_api_key)
            .json(&payload)
            .send()
            .await
        {
            Ok(value) if value.status().is_success() => {
                response = Some(value.json::<Value>().await?);
                break;
            }
            Ok(value)
                if attempt + 1 < SKILL_AUTHOR_TRANSPORT_ATTEMPTS
                    && retryable_author_status(value.status().as_u16()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Ok(value) => {
                return Err(value
                    .error_for_status()
                    .expect_err("non-success response")
                    .into());
            }
            Err(error)
                if attempt + 1 < SKILL_AUTHOR_TRANSPORT_ATTEMPTS
                    && (error.is_timeout() || error.is_connect()) =>
            {
                sleep(Duration::from_millis(250 * (1 << attempt))).await;
            }
            Err(error) => return Err(error.into()),
        }
    }
    let response = response.ok_or_else(|| anyhow::anyhow!("SKILL_AUTHOR_TRANSPORT_EXHAUSTED"))?;
    let arguments = response
        .pointer("/choices/0/message/tool_calls/0/function/arguments")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("skill_author LLM returned no submit_skill call"))?;
    Ok(serde_json::from_str(arguments)?)
}

fn retryable_author_status(status: u16) -> bool {
    status == 429 || (500..=599).contains(&status)
}

async fn canonical_repository(inputs: &Value, allowed: &[PathBuf]) -> anyhow::Result<PathBuf> {
    let requested = PathBuf::from(string(inputs, "repository")?);
    if requested
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        anyhow::bail!("REPOSITORY_PATH_ESCAPE");
    }
    let canonical = fs::canonicalize(&requested)
        .await
        .context("canonicalize repository")?;
    let mut permitted = false;
    for root in allowed {
        if let Ok(root) = fs::canonicalize(root).await {
            permitted |= canonical.starts_with(root);
        }
    }
    if !permitted {
        anyhow::bail!("REPOSITORY_NOT_MANAGED");
    }
    if !fs::try_exists(canonical.join(".git")).await? {
        anyhow::bail!("REPOSITORY_NOT_GIT");
    }
    Ok(canonical)
}

async fn create_worktree(
    repository: &Path,
    managed_root: &Path,
    ticket_id: Uuid,
    run_id: Uuid,
    base_ref: &str,
) -> anyhow::Result<(PathBuf, String)> {
    let workspace = managed_root.join(run_id.to_string()).join("worktree");
    fs::create_dir_all(workspace.parent().expect("run parent")).await?;
    let branch = format!("openos/{ticket_id}-{}", &run_id.to_string()[..8]);
    let base_sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["rev-parse", base_ref]),
        "git base ref",
    )
    .await?;
    checked(
        Command::new("git")
            .arg("-C")
            .arg(repository)
            .args(["worktree", "add", "-b"])
            .arg(&branch)
            .arg(&workspace)
            .arg(base_ref),
        "git worktree add",
    )
    .await?;
    Ok((workspace, base_sha))
}

struct EngineOutput {
    exit_status: i32,
    stdout: String,
    stderr: String,
    session_id: Option<String>,
}

async fn run_opencode(
    config: &Config,
    job: &WorkerJob,
    run_id: Uuid,
    workspace: &Path,
    prompt: &str,
    store: &RunStore,
) -> anyhow::Result<EngineOutput> {
    let mut child = Command::new(&config.opencode_binary)
        .args([
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "50",
            "--bare",
            "--dangerously-skip-permissions",
        ])
        .arg(prompt)
        .current_dir(workspace)
        .env("SHELL", &config.shell_binary)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("OPENCODE_INVOKED_BY", "openagents-rust")
        .env("OPENTICKET_TICKET_ID", job.ticket_id.to_string())
        .env("OPENTICKET_CORRELATION_ID", job.correlation_id.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdout = child.stdout.take().context("OpenCode stdout")?;
    let stderr = child.stderr.take().context("OpenCode stderr")?;
    let store_copy = store.clone();
    let stdout_task = tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        let mut raw = String::new();
        let mut session = None;
        while let Some(line) = lines.next_line().await? {
            if let Ok(event) = serde_json::from_str::<Value>(&line) {
                session = session.or_else(|| session_id(&event));
                store_copy
                    .update(
                        run_id,
                        RunStatus::Running,
                        "opencode.event",
                        safe_event(&event),
                    )
                    .await;
            }
            raw.push_str(&line);
            raw.push('\n');
        }
        Ok::<_, anyhow::Error>((raw, session))
    });
    let stderr_task = tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        let mut raw = String::new();
        while let Some(line) = lines.next_line().await? {
            raw.push_str(&line);
            raw.push('\n');
        }
        Ok::<_, anyhow::Error>(raw)
    });
    let status = tokio::select! {
        result = timeout(config.opencode_timeout, child.wait()) => result
            .map_err(|_| anyhow::anyhow!("OPENCODE_TIMEOUT"))??,
        _ = wait_cancelled(store, run_id) => {
            child.kill().await?;
            anyhow::bail!("RUN_CANCELLED");
        }
    };
    let (stdout, session_id) = stdout_task.await??;
    let stderr = stderr_task.await??;
    Ok(EngineOutput {
        exit_status: status.code().unwrap_or(-1),
        stdout,
        stderr,
        session_id,
    })
}

async fn run_tests(
    workspace: &Path,
    commands: &[String],
    run_id: Uuid,
    store: &RunStore,
) -> anyhow::Result<Vec<TestEvidence>> {
    let mut evidence = Vec::new();
    for command in commands {
        ensure_not_cancelled(store, run_id).await?;
        store
            .update(
                run_id,
                RunStatus::Running,
                "test.started",
                json!({"command":command}),
            )
            .await;
        let output = Command::new("sh")
            .args(["-lc", command])
            .current_dir(workspace)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .output()
            .await?;
        let path = workspace
            .parent()
            .expect("run root")
            .join(format!("test-{}.log", evidence.len() + 1));
        let mut data = output.stdout;
        data.extend_from_slice(&output.stderr);
        fs::write(&path, &data).await?;
        let exit_status = output.status.code().unwrap_or(-1);
        store
            .update(
                run_id,
                RunStatus::Running,
                "test.completed",
                json!({"command":command,"exit_status":exit_status}),
            )
            .await;
        evidence.push(TestEvidence {
            command: command.clone(),
            exit_status,
            passed: u32::from(exit_status == 0),
            failed: u32::from(exit_status != 0),
            output_uri: Some(file_uri(&path)),
        });
        if exit_status != 0 {
            break;
        }
    }
    Ok(evidence)
}

async fn wait_cancelled(store: &RunStore, run_id: Uuid) {
    loop {
        if store.is_cancelled(run_id).await {
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
}

async fn ensure_not_cancelled(store: &RunStore, run_id: Uuid) -> anyhow::Result<()> {
    if store.is_cancelled(run_id).await {
        anyhow::bail!("RUN_CANCELLED");
    }
    Ok(())
}

async fn commit_changes(
    workspace: &Path,
    repository: &Path,
    base_sha: &str,
    ticket_id: Uuid,
    run_id: Uuid,
    sign_commit: bool,
) -> anyhow::Result<GitEvidence> {
    checked(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["add", "-A"]),
        "git add",
    )
    .await?;
    let diff = checked_output(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["diff", "--cached", "--quiet"]),
    )
    .await?;
    if !diff.success() {
        checked(
            Command::new("git")
                .arg("-C")
                .arg(workspace)
                .args([
                    "-c",
                    "user.name=OpenAgents",
                    "-c",
                    "user.email=openagents@openos.local",
                    "-c",
                    if sign_commit {
                        "commit.gpgsign=true"
                    } else {
                        "commit.gpgsign=false"
                    },
                    "commit",
                    "-m",
                ])
                .arg(format!(
                    "OpenTicket {ticket_id}: validated worker run {run_id}"
                )),
            "git commit",
        )
        .await?;
    }
    let sha = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["rev-parse", "HEAD"]),
        "git rev-parse",
    )
    .await?;
    if sha == base_sha {
        anyhow::bail!("OPENCODE_PRODUCED_NO_CHANGES");
    }
    let branch = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["branch", "--show-current"]),
        "git branch",
    )
    .await?;
    let status = output_text(
        Command::new("git")
            .arg("-C")
            .arg(workspace)
            .args(["status", "--porcelain"]),
        "git status",
    )
    .await?;
    Ok(GitEvidence {
        repository: repository.display().to_string(),
        worktree: workspace.display().to_string(),
        branch,
        commit_sha: sha,
        clean: status.is_empty(),
    })
}

async fn persist_events(root: &Path, run_id: Uuid, data: &str) -> anyhow::Result<WorkerArtifact> {
    let path = root.join(run_id.to_string()).join("opencode-events.jsonl");
    fs::write(&path, data).await?;
    let hash = format!("{:x}", Sha256::digest(data.as_bytes()));
    Ok(WorkerArtifact {
        kind: "opencode_event_stream".into(),
        name: "OpenCode events".into(),
        uri: file_uri(&path),
        sha256: Some(hash),
        metadata: json!({"bytes":data.len()}),
    })
}

async fn checked(command: &mut Command, name: &str) -> anyhow::Result<()> {
    let output = command.output().await?;
    if !output.status.success() {
        anyhow::bail!(
            "{name}: {}",
            sanitize(&String::from_utf8_lossy(&output.stderr))
        );
    }
    Ok(())
}

async fn checked_output(command: &mut Command) -> anyhow::Result<std::process::ExitStatus> {
    Ok(command
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await?)
}

async fn output_text(command: &mut Command, name: &str) -> anyhow::Result<String> {
    let output = command.output().await?;
    if !output.status.success() {
        anyhow::bail!("{name} failed");
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn string(value: &Value, key: &str) -> anyhow::Result<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| anyhow::anyhow!("INPUT_{key}_REQUIRED"))
}

fn string_array(value: &Value, key: &str) -> anyhow::Result<Vec<String>> {
    let values: Vec<String> = value
        .get(key)
        .cloned()
        .map(serde_json::from_value)
        .transpose()?
        .unwrap_or_default();
    if values.is_empty() {
        anyhow::bail!("INPUT_{key}_REQUIRED");
    }
    Ok(values)
}

fn session_id(value: &Value) -> Option<String> {
    value
        .get("session_id")
        .or_else(|| value.get("sessionId"))
        .or_else(|| {
            value
                .get("message")
                .and_then(|message| message.get("session_id"))
        })
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn safe_event(value: &Value) -> Value {
    json!({"type":value.get("type"),"subtype":value.get("subtype"),"session_id":value.get("session_id")})
}

fn sanitize(value: &str) -> String {
    value
        .lines()
        .take(100)
        .map(|line| {
            if line.to_ascii_lowercase().contains("authorization") || line.contains("sk-") {
                "[redacted]"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SecretKind {
    ApiKey,
    BearerToken,
    PemPrivateKey,
    ProviderToken,
}

impl SecretKind {
    fn label(self) -> &'static str {
        match self {
            Self::ApiKey => "api_key",
            Self::BearerToken => "bearer_token",
            Self::PemPrivateKey => "pem_private_key",
            Self::ProviderToken => "provider_token",
        }
    }
}

fn contains_secret(value: &str) -> bool {
    detect_secret(value).is_some()
}

fn detect_secret(value: &str) -> Option<SecretKind> {
    let lower = value.to_ascii_lowercase();
    if [
        "-----begin private key-----",
        "-----begin rsa private key-----",
        "-----begin ec private key-----",
        "-----begin openssh private key-----",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
    {
        return Some(SecretKind::PemPrivateKey);
    }
    if ["sk-", "ghp_", "github_pat_", "xoxb-", "xoxp-"]
        .iter()
        .any(|prefix| has_prefixed_token(&lower, prefix, 16))
    {
        return Some(SecretKind::ProviderToken);
    }
    if ["authorization: bearer", "authorization=bearer"]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 20))
    {
        return Some(SecretKind::BearerToken);
    }
    if ["api_key=", "api-key=", "apikey="]
        .iter()
        .any(|marker| has_assigned_credential(&lower, marker, 16))
    {
        return Some(SecretKind::ApiKey);
    }
    None
}

fn has_prefixed_token(value: &str, prefix: &str, min_suffix: usize) -> bool {
    value.match_indices(prefix).any(|(index, _)| {
        let suffix = &value[index + prefix.len()..];
        credential_token(suffix).chars().count() >= min_suffix
    })
}

fn has_assigned_credential(value: &str, marker: &str, min_length: usize) -> bool {
    value.match_indices(marker).any(|(index, _)| {
        let suffix = &value[index + marker.len()..];
        let token = credential_token(suffix);
        token.chars().count() >= min_length && !is_placeholder(token)
    })
}

fn credential_token(value: &str) -> &str {
    let value = value.trim_start_matches(|character: char| {
        character.is_ascii_whitespace() || matches!(character, '\\' | '\'' | '"' | ':' | '=')
    });
    let end = value
        .find(|character: char| {
            !(character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.'))
        })
        .unwrap_or(value.len());
    &value[..end]
}

fn is_placeholder(value: &str) -> bool {
    [
        "changeme",
        "dummy",
        "example",
        "none",
        "null",
        "placeholder",
        "redacted",
        "replace",
        "test",
        "your",
    ]
    .iter()
    .any(|prefix| value.starts_with(prefix))
}

fn nonempty(value: String) -> Option<String> {
    (!value.is_empty()).then_some(value)
}
fn file_uri(path: &Path) -> String {
    format!("file://{}", path.display())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn rejects_path_escape_and_unmanaged_repository() {
        let temp = tempfile::tempdir().unwrap();
        let value = json!({"repository":"../outside"});
        assert!(canonical_repository(&value, &[temp.path().into()])
            .await
            .is_err());
    }

    #[test]
    fn strips_secret_bearing_stderr() {
        assert_eq!(
            sanitize("ok\nAuthorization: Bearer abc\nsk-secret"),
            "ok\n[redacted]\n[redacted]"
        );
    }

    #[test]
    fn credential_guard_ignores_source_identifiers_and_placeholders() {
        for value in [
            "private_key",
            "document the sk- provider prefix",
            "API_KEY=",
            "API_KEY=$OPENAI_API_KEY",
            "API_KEY=placeholder-value-long-enough",
            "Authorization: Bearer <token>",
        ] {
            assert_eq!(detect_secret(value), None, "false positive for {value}");
        }
    }

    #[test]
    fn credential_guard_detects_realistic_secret_shapes() {
        assert_eq!(
            detect_secret("-----BEGIN PRIVATE KEY-----\\nbase64-material"),
            Some(SecretKind::PemPrivateKey)
        );
        assert_eq!(
            detect_secret("sk-proj-fakecredentialmaterial123456"),
            Some(SecretKind::ProviderToken)
        );
        assert_eq!(
            detect_secret("API_KEY=fakeCredentialValue123456"),
            Some(SecretKind::ApiKey)
        );
        assert_eq!(
            detect_secret("Authorization: Bearer fake.jwt.token-value-123456"),
            Some(SecretKind::BearerToken)
        );
    }

    #[test]
    fn changed_file_artifact_contains_exact_files_and_digest() {
        let temp = tempfile::tempdir().unwrap();
        let repository = temp.path();
        let git = |args: &[&str]| {
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(repository)
                .args(args)
                .output()
                .unwrap();
            assert!(output.status.success(), "git {:?} failed", args);
            String::from_utf8(output.stdout).unwrap().trim().to_string()
        };
        git(&["init", "--quiet"]);
        git(&["config", "user.email", "worker-test@openos.local"]);
        git(&["config", "user.name", "OpenAgents Worker Test"]);
        std::fs::write(repository.join("README.md"), "base\n").unwrap();
        git(&["add", "README.md"]);
        git(&["commit", "--quiet", "-m", "base"]);
        let base_sha = git(&["rev-parse", "HEAD"]);

        std::fs::write(repository.join("README.md"), "changed\n").unwrap();
        std::fs::create_dir(repository.join("src")).unwrap();
        std::fs::write(repository.join("src/lib.rs"), "pub fn changed() {}\n").unwrap();
        git(&["add", "README.md", "src/lib.rs"]);
        git(&["commit", "--quiet", "-m", "change"]);
        let commit_sha = git(&["rev-parse", "HEAD"]);

        let artifact = changed_files_artifact(repository, &base_sha, &commit_sha).unwrap();
        let expected_output = b"README.md\nsrc/lib.rs\n";
        assert_eq!(artifact.kind, "changed_files");
        assert_eq!(artifact.uri, format!("git://{commit_sha}/changed-files"));
        assert_eq!(
            artifact.sha256.as_deref(),
            Some(format!("{:x}", Sha256::digest(expected_output)).as_str())
        );
        assert_eq!(
            artifact.metadata,
            json!({
                "base_sha":base_sha,
                "commit_sha":commit_sha,
                "files":["README.md", "src/lib.rs"],
            })
        );
    }

    #[tokio::test]
    async fn rejects_private_research_source_addresses() {
        assert!(validate_public_https_url("https://127.0.0.1/private")
            .await
            .is_err());
        assert!(validate_public_https_url("https://[::1]/private")
            .await
            .is_err());
    }

    #[test]
    fn requires_authoritative_selection_from_extracted_sources() {
        let extracted = vec![
            ResearchSource {
                url: "https://docs.example.org/spec".into(),
                title: "Specification".into(),
                text: "x".repeat(200),
            },
            ResearchSource {
                url: "https://standards.example.net/rules".into(),
                title: "Rules".into(),
                text: "y".repeat(200),
            },
        ];
        let authored = json!({"sources":[
            {"url":extracted[0].url,"authority":"Official specification publisher"},
            {"url":extracted[1].url,"authority":"Independent standards authority"}
        ]});
        assert_eq!(
            select_authoritative_sources(&authored, &extracted)
                .unwrap()
                .len(),
            2
        );

        let fabricated = json!({"sources":[
            {"url":"https://unseen.example.com","authority":"Official specification publisher"},
            {"url":extracted[1].url,"authority":"Independent standards authority"}
        ]});
        assert!(select_authoritative_sources(&fabricated, &extracted).is_err());
    }

    #[test]
    fn diversification_excludes_hosts_already_extracted() {
        let hosts = ["docs.example.org".to_string()]
            .into_iter()
            .collect::<std::collections::HashSet<_>>();
        let query = diversify_research_query("invoice validation", &hosts);

        assert!(query.contains("invoice validation official primary source"));
        assert!(query.contains("-site:docs.example.org"));
    }

    #[test]
    fn concise_search_variant_preserves_domain_terms() {
        let variants = search_query_variants(
            "Using authoritative documentation only, research the current Peppol BIS Billing 3.0 validation procedure, prioritizing OpenPeppol Schematron artifacts.",
        );
        assert!(variants[0].contains("Peppol BIS Billing 3.0 validation"));
        assert!(variants[0].contains("OpenPeppol Schematron"));
        assert_eq!(variants.len(), 2);
    }

    #[test]
    fn ranks_query_matching_official_sources_before_commercial_explainers() {
        let mut candidates = vec![
            (
                "https://vendor.example.com/blog/validator".into(),
                "Invoice validator".into(),
            ),
            (
                "https://github.com/OpenPEPPOL/peppol-bis-invoice-3/tree/master/rules".into(),
                "Official rules".into(),
            ),
            (
                "https://docs.peppol.eu/poacc/billing/3.0/".into(),
                "Peppol BIS Billing specification".into(),
            ),
        ];
        rank_search_candidates(
            &mut candidates,
            "Peppol BIS Billing OpenPeppol official rules",
        );
        assert!(
            candidates[0].0.contains("OpenPEPPOL") || candidates[0].0.contains("docs.peppol.eu")
        );
        assert!(!candidates[0].0.contains("vendor.example.com"));
    }

    #[test]
    fn authoring_validation_excludes_control_plane_lifecycle_criteria() {
        let criteria = vec![
            "Defines repeatable invoice checks.".to_string(),
            "Is activated privately only after explicit user confirmation.".to_string(),
            "Authoring occurs only after confirmation.".to_string(),
            "Retries the originating request exactly once after successful activation.".to_string(),
        ];
        assert_eq!(
            authoring_success_criteria(&criteria),
            vec!["Defines repeatable invoice checks."]
        );
    }

    #[test]
    fn retries_only_transient_author_transport_statuses() {
        assert!(retryable_author_status(429));
        assert!(retryable_author_status(502));
        assert!(!retryable_author_status(400));
        assert!(!retryable_author_status(401));
    }

    #[test]
    fn requires_grounded_evidence_for_every_approved_criterion() {
        let criteria = vec!["Validate syntax before business rules".to_string()];
        let generalization = json!({
            "capability":"Validate authoritative business documents",
            "parameters":[{"name":"target","description":"Document system to validate","required":true,"source":"user","schema":{"type":"string"}}],
            "instanceBindings":{"target":"network one"},
            "invariants":["Use authoritative rules"],
            "discoveryStrategy":["Resolve the target owner","Discover independent official rules"],
            "validationScenarios":[
                {"name":"network one","bindings":{"target":"network one"}},
                {"name":"network two","bindings":{"target":"network two"}}
            ]
        });
        let sources = vec![
            json!({"url":"https://docs.example.org/spec"}),
            json!({"url":"https://standards.example.net/rules"}),
        ];
        let authored = json!({"validation":{"passed":true,"criteria":[{
            "criterion":criteria[0],"passed":true,
            "evidence":"The procedure explicitly orders syntax validation before rules."
        }],"generality_scenarios":[
            {"name":"network one","passed":true,"evidence":"The runtime target parameter selects network one rules."},
            {"name":"network two","passed":true,"evidence":"The runtime target parameter selects network two rules."}
        ]}});
        let body = "## Runtime parameters\nUse `target`. The procedure explicitly orders syntax validation before rules. Use https://docs.example.org/spec before applying https://standards.example.net/rules.";
        let extracted = vec![
            ResearchSource {
                url: "https://docs.example.org/spec".into(),
                title: "Specification".into(),
                text: "Official syntax requirements.".into(),
            },
            ResearchSource {
                url: "https://standards.example.net/rules".into(),
                title: "Rules".into(),
                text: "Official business rules.".into(),
            },
        ];
        assert!(validate_authored_skill(
            &authored,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_ok());

        let missing = json!({"validation":{"passed":true,"criteria":[]}});
        assert!(validate_authored_skill(
            &missing,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_err());

        let unsupported = json!({"validation":{"passed":true,"criteria":[{
            "criterion":criteria[0],"passed":true,
            "evidence":"This assertion appears in neither the skill nor its selected sources."
        }]}});
        assert!(validate_authored_skill(
            &unsupported,
            &criteria,
            &generalization,
            body,
            &sources,
            &extracted
        )
        .is_err());
    }

    #[test]
    fn documentation_skill_generalizes_across_repository_and_website_discovery() {
        let generalization = json!({
            "capability":"Discover and ingest authoritative application documentation",
            "parameters":[
                {"name":"target","description":"Application whose docs are required","required":true,"source":"user","schema":{"type":"string"}},
                {"name":"source_hint","description":"Available source discovery hint","required":true,"source":"discovery","schema":{"type":"string"}},
                {"name":"destination","description":"Registered ingestion destination","required":true,"source":"organization_config","schema":{"type":"string"}}
            ],
            "instanceBindings":{"target":"OpenFoo","source_hint":"local_source","destination":"openbrain_docs"},
            "invariants":["Verify provenance and never ingest secrets"],
            "discoveryStrategy":["Inspect source code and Git remotes","Otherwise verify official website ownership"],
            "validationScenarios":[
                {"name":"source repository","bindings":{"target":"OpenFoo","source_hint":"local_source","destination":"openbrain_docs"}},
                {"name":"official website","bindings":{"target":"External SaaS","source_hint":"website","destination":"openbrain_docs"}}
            ]
        });
        let body = "## Runtime parameters\nUse `target`, `source_hint`, and `destination`. If source is available inspect Git remotes; otherwise resolve the official website and owner.";

        assert!(validate_generalization_contract(&generalization).is_ok());
        assert!(validate_parameterized_body(&generalization, body).is_ok());

        let missing_instance_binding = json!({
            "capability":generalization["capability"],
            "parameters":generalization["parameters"],
            "instanceBindings":{"target":"OpenFoo","source_hint":"local_source"},
            "invariants":generalization["invariants"],
            "discoveryStrategy":generalization["discoveryStrategy"],
            "validationScenarios":generalization["validationScenarios"]
        });
        assert!(validate_generalization_contract(&missing_instance_binding).is_err());

        let hardcoded = format!("{body} Always fetch https://vendor.example/docs.");
        let with_url_binding = json!({
            "capability":"Discover and ingest authoritative application documentation",
            "parameters":generalization["parameters"],
            "instanceBindings":{"target":"Vendor","source_hint":"website","destination":"openbrain_docs","website":"https://vendor.example/docs"},
            "invariants":generalization["invariants"],
            "discoveryStrategy":generalization["discoveryStrategy"],
            "validationScenarios":generalization["validationScenarios"]
        });
        assert!(validate_parameterized_body(&with_url_binding, &hardcoded).is_err());
    }

    #[test]
    fn engineering_jobs_receive_bounded_generalization_rules() {
        let prompt = engineering_prompt("Implement documentation ingestion for OpenFoo.");
        assert!(prompt.contains("current working directory is the only permitted"));
        assert!(prompt.contains("repository paths from the task as source identity metadata"));
        assert!(prompt.contains("never edit an absolute repository path"));
        assert!(prompt.contains("facts that vary between valid instances"));
        assert!(prompt.contains("smallest reusable boundary"));
        assert!(prompt.contains("materially different instance"));
        assert!(!prompt.contains("Always use OpenFoo"));
    }

    #[test]
    fn parses_primary_and_lite_search_result_shapes() {
        let mut results = Vec::new();
        append_search_results(
            r#"<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.org%2Fspec">Specification</a>"#,
            "a.result__a",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fstandards.example.net%2Frules" class="result-link">Rules</a>"#,
            "a.result-link",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<li class="b_algo"><h2><a href="https://primary.example.com/guide">Guide</a></h2></li>"#,
            "li.b_algo h2 a",
            &mut results,
        )
        .unwrap();
        append_search_results(
            r#"<div class="snippet" data-type="web"><a href="https://authority.example.edu/standard">Standard</a></div>"#,
            "div.snippet[data-type=\"web\"] a[href]",
            &mut results,
        )
        .unwrap();
        assert_eq!(results.len(), 4);
        assert_eq!(results[0].0, "https://docs.example.org/spec");
        assert_eq!(results[1].0, "https://standards.example.net/rules");
        assert_eq!(results[2].0, "https://primary.example.com/guide");
        assert_eq!(results[3].0, "https://authority.example.edu/standard");
    }

    #[test]
    fn decodes_bing_tracking_urls_and_rejects_search_pages() {
        let target = "https://docs.example.org/spec";
        let encoded = URL_SAFE_NO_PAD.encode(target);
        let tracked = format!("https://www.bing.com/ck/a?u=a1{encoded}&ntb=1");

        assert_eq!(
            normalize_search_result_url(&tracked).as_deref(),
            Some(target)
        );
        assert!(normalize_search_result_url("https://www.bing.com/search?q=spec").is_none());
    }
}
