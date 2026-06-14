const GH_REPO     = "ctrovisco-blip/stock-screener";
const GH_WORKFLOW = "screener.yml";

Deno.serve(async (req) => {
  const cors = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  };

  if (req.method === "OPTIONS") return new Response(null, { headers: cors });

  try {
    const { tickers, mode, profile_id } = await req.json();

    if (!tickers || !profile_id)
      return new Response(JSON.stringify({ error: "Missing tickers or profile_id" }),
        { status: 400, headers: { ...cors, "Content-Type": "application/json" } });

    /* Validate profile exists */
    const sbUrl = Deno.env.get("SUPABASE_URL")!;
    const sbKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const check = await fetch(`${sbUrl}/rest/v1/profiles?id=eq.${profile_id}&select=id`, {
      headers: { "apikey": sbKey, "Authorization": `Bearer ${sbKey}` }
    });
    const rows = await check.json();
    if (!Array.isArray(rows) || rows.length === 0)
      return new Response(JSON.stringify({ error: "Perfil inválido" }),
        { status: 403, headers: { ...cors, "Content-Type": "application/json" } });

    /* Dispatch GitHub Actions workflow */
    const pat = Deno.env.get("GITHUB_PAT")!;
    const gh = await fetch(
      `https://api.github.com/repos/${GH_REPO}/actions/workflows/${GH_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${pat}`,
          "Accept":        "application/vnd.github+json",
          "Content-Type":  "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { tickers, mode: mode || "add" } }),
      }
    );

    if (gh.status === 204)
      return new Response(JSON.stringify({ ok: true }),
        { status: 200, headers: { ...cors, "Content-Type": "application/json" } });

    const err = await gh.json().catch(() => ({}));
    return new Response(JSON.stringify({ error: err.message || `GitHub ${gh.status}` }),
      { status: gh.status, headers: { ...cors, "Content-Type": "application/json" } });

  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }),
      { status: 500, headers: { ...cors, "Content-Type": "application/json" } });
  }
});
