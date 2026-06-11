// Netlify Scheduled Function — fires the Instagram lead report on the 1st and
// 16th of each month by calling the backend's token-protected POST
// /jobs/lead-report. Schedule is configured in netlify.toml.
//
// Reuses the same env vars as all other job functions:
//   BACKEND_ORIGIN     e.g. https://lif-crm.onrender.com
//   META_REFRESH_TOKEN must equal the backend's META_VERIFY_TOKEN
export default async () => {
  const origin = process.env.BACKEND_ORIGIN;
  const token  = process.env.META_REFRESH_TOKEN;
  if (!origin || !token) {
    return new Response("BACKEND_ORIGIN / META_REFRESH_TOKEN not configured", { status: 500 });
  }
  try {
    const res  = await fetch(
      `${origin}/jobs/lead-report?token=${encodeURIComponent(token)}`,
      { method: "POST" },
    );
    const body = await res.text();
    console.log(`lead-report -> ${res.status}: ${body}`);
    return new Response(body, { status: res.status });
  } catch (err) {
    console.error("lead-report failed", err);
    return new Response(String(err), { status: 502 });
  }
};
