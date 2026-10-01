#!/usr/bin/env node

const SEARCH_ENGINES = {
  duckduckgo: (query) => `https://duckduckgo.com/?q=${encodeURIComponent(query)}`,
  bing: (query) => `https://www.bing.com/search?q=${encodeURIComponent(query)}`,
};

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function clampInt(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.min(Math.max(parsed, min), max);
}

function normalizeEngine(value) {
  const raw = String(value || "duckduckgo").toLowerCase();
  if (raw === "ddg" || raw === "duck" || raw === "duckduckgo.com") return "duckduckgo";
  if (raw === "bing" || raw === "bing.com") return "bing";
  return SEARCH_ENGINES[raw] ? raw : "duckduckgo";
}

function isPrivateIpv4(hostname) {
  const parts = hostname.split(".").map((part) => Number.parseInt(part, 10));
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) return false;
  const [a, b] = parts;
  return (
    a === 10 ||
    a === 127 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 169 && b === 254) ||
    a === 0
  );
}

function validatePublicHttpUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl || "").trim());
  } catch {
    throw new Error("invalid URL");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("only http and https URLs are supported");
  }
  const host = parsed.hostname.toLowerCase();
  if (
    !host ||
    host === "localhost" ||
    host === "localhost.localdomain" ||
    host.endsWith(".local") ||
    (host.includes(":") &&
      (host === "::1" || host.startsWith("fc") || host.startsWith("fd") || host.startsWith("fe80"))) ||
    isPrivateIpv4(host)
  ) {
    throw new Error("blocked private/local URL");
  }
  return parsed.href;
}

async function loadChromium() {
  try {
    const mod = await import("playwright");
    return mod.chromium;
  } catch (firstError) {
    try {
      const mod = await import("@playwright/test");
      return mod.chromium;
    } catch (secondError) {
      throw new Error(
        "Playwright is not installed. Run `npm install` and `npx playwright install chromium`. " +
          `Details: ${secondError?.message || firstError?.message || "module import failed"}`
      );
    }
  }
}

async function createBrowser(timeoutMs) {
  const chromium = await loadChromium();
  const browser = await chromium.launch({
    headless: true,
    timeout: timeoutMs,
  });
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ClawCrossWebSearch/1.0",
    locale: "en-US",
    viewport: { width: 1365, height: 900 },
  });
  return { browser, context };
}

async function withPage(timeoutMs, callback) {
  const { browser, context } = await createBrowser(timeoutMs);
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutMs);
    page.setDefaultNavigationTimeout(timeoutMs);
    return await callback(page);
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

async function waitForSettledPage(page, timeoutMs) {
  try {
    await page.waitForLoadState("networkidle", { timeout: Math.min(5000, timeoutMs) });
  } catch {
    // Search and modern JS pages often keep long-lived requests open.
  }
}

async function runSearch(input) {
  const query = String(input.query || "").trim();
  if (!query) throw new Error("query must not be empty");

  const engine = normalizeEngine(input.engine);
  const maxResults = clampInt(input.maxResults, 8, 1, 25);
  const timeoutMs = clampInt(input.timeoutMs, 15000, 3000, 90000);
  const searchUrl = SEARCH_ENGINES[engine](query);

  return await withPage(timeoutMs, async (page) => {
    await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    await waitForSettledPage(page, timeoutMs);
    try {
      await page.waitForSelector("a[href]", { timeout: Math.min(8000, timeoutMs) });
    } catch {
      // The page may still have useful text or may be blocked by the search engine.
    }

    const rawResults = await page.evaluate(
      ({ engine: pageEngine, maxResults: pageMaxResults }) => {
        const engineHosts = new Set([
          "duckduckgo.com",
          "www.duckduckgo.com",
          "bing.com",
          "www.bing.com",
          "microsoft.com",
          "www.microsoft.com",
          "msn.com",
          "www.msn.com",
        ]);

        function collapseText(value) {
          return String(value || "").replace(/\s+/g, " ").trim();
        }

        function normalizeHref(rawHref) {
          if (!rawHref) return "";
          let parsed;
          try {
            parsed = new URL(rawHref, window.location.href);
          } catch {
            return "";
          }
          if (!["http:", "https:"].includes(parsed.protocol)) return "";
          if (parsed.hostname.endsWith("duckduckgo.com") && parsed.pathname === "/l/") {
            const uddg = parsed.searchParams.get("uddg");
            if (uddg) {
              try {
                parsed = new URL(uddg);
              } catch {
                return "";
              }
            }
          }
          return parsed.href;
        }

        function shouldSkipUrl(url) {
          let parsed;
          try {
            parsed = new URL(url);
          } catch {
            return true;
          }
          const host = parsed.hostname.toLowerCase();
          if (engineHosts.has(host)) return true;
          if (host.endsWith(".duckduckgo.com") || host.endsWith(".bing.com")) return true;
          return false;
        }

        function snippetFrom(container, anchor) {
          const selectors = [
            "[data-result='snippet']",
            ".result__snippet",
            ".snippet",
            ".b_caption p",
            ".b_snippet",
            "p",
          ];
          for (const selector of selectors) {
            const node = container?.querySelector(selector);
            const text = collapseText(node?.innerText || node?.textContent || "");
            if (text && text !== collapseText(anchor?.innerText || anchor?.textContent || "")) {
              return text;
            }
          }
          const containerText = collapseText(container?.innerText || container?.textContent || "");
          const titleText = collapseText(anchor?.innerText || anchor?.textContent || "");
          if (containerText.startsWith(titleText)) {
            return collapseText(containerText.slice(titleText.length)).slice(0, 500);
          }
          return containerText.slice(0, 500);
        }

        const output = [];
        const seen = new Set();
        const containers = [
          ...document.querySelectorAll(
            "[data-testid='result'], article, .result, .results_links, .web-result, li.b_algo, .b_algo"
          ),
        ];

        function addCandidate(anchor, container) {
          const url = normalizeHref(anchor?.getAttribute("href") || anchor?.href || "");
          if (!url || seen.has(url) || shouldSkipUrl(url)) return;
          const title = collapseText(anchor.innerText || anchor.textContent || "");
          if (!title || title.length < 2) return;
          seen.add(url);
          output.push({
            title,
            href: url,
            body: snippetFrom(container, anchor),
            source: pageEngine,
          });
        }

        for (const container of containers) {
          const anchors = [...container.querySelectorAll("a[href]")];
          const preferred =
            anchors.find((anchor) =>
              anchor.matches(
                ".result__a, [data-testid='result-title-a'], h2 a, h3 a, .b_algo h2 a"
              )
            ) || anchors[0];
          addCandidate(preferred, container);
          if (output.length >= pageMaxResults * 2) break;
        }

        if (output.length < pageMaxResults) {
          for (const anchor of document.querySelectorAll("a[href]")) {
            addCandidate(anchor, anchor.closest("article, li, div, section") || anchor.parentElement);
            if (output.length >= pageMaxResults * 2) break;
          }
        }

        return output.slice(0, pageMaxResults * 2);
      },
      { engine, maxResults }
    );

    return {
      ok: true,
      provider: "browser",
      engine,
      query,
      search_url: searchUrl,
      final_url: page.url(),
      result_count: rawResults.length,
      raw_results: rawResults,
    };
  });
}

async function runFetch(input) {
  const url = validatePublicHttpUrl(input.url);
  const maxChars = clampInt(input.maxChars, 12000, 500, 50000);
  const timeoutMs = clampInt(input.timeoutMs, 15000, 3000, 90000);

  return await withPage(timeoutMs, async (page) => {
    const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    await waitForSettledPage(page, timeoutMs);
    const pageData = await page.evaluate(() => {
      const text = String(document.body?.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
      return {
        title: document.title || "",
        text,
      };
    });
    const text = String(pageData.text || "");
    return {
      ok: true,
      provider: "browser",
      url,
      final_url: page.url(),
      status_code: response?.status() || null,
      content_type: response?.headers()?.["content-type"] || "",
      title: String(pageData.title || ""),
      text: text.slice(0, maxChars),
      truncated: text.length > maxChars,
      chars: Math.min(text.length, maxChars),
    };
  });
}

async function main() {
  const rawInput = await readStdin();
  const input = rawInput.trim() ? JSON.parse(rawInput) : {};
  const mode = String(input.mode || "").toLowerCase();
  let result;
  if (mode === "search") {
    result = await runSearch(input);
  } else if (mode === "fetch") {
    result = await runFetch(input);
  } else {
    throw new Error("mode must be search or fetch");
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error?.message || String(error)}\n`);
  process.stdout.write(
    `${JSON.stringify({
      ok: false,
      provider: "browser",
      error: error?.message || String(error),
    })}\n`
  );
  process.exitCode = 1;
});
