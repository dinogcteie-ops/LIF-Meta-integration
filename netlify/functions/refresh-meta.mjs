// Netlify Scheduled Function — pulls the latest Meta Ads metrics once a day by
// calling the backend's POST /meta/refresh endpoint (token-protected). The
// schedule is configured in netlify.toml ([functions."refresh-meta"]).
//
// Required Netlify env vars:
//   BACKEND_ORIGIN     e.g. https://lif-crm.onrender.com
//   META_REFRESH_TOKEN must equal the backend's META_VERIFY_TOKEN
export default async () => {
  const origin = process.env.BACKEND_ORIGIN;
  const token = process.env.META_REFRESH_TOKEN;
  if (!origin || !token) {
    return new Response("BACKEND_ORIGIN / META_REFRESH_TOKEN not configured", { status: 500 });
  }
  try {
    const res = await fetch(`${origin}/meta/refresh?token=${encodeURIComponent(token)}`, {
      method: "POST",
    });
    const body = await res.text();
    console.log(`meta refresh -> ${res.status}: ${body}`);
    return new Response(body, { status: res.status });
  } catch (err) {
    console.error("meta refresh failed", err);
    return new Response(String(err), { status: 502 });
  }
};
