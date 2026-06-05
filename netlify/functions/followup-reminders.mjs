// Netlify Scheduled Function — fires the daily follow-up reminder digest by
// calling the backend's token-protected POST /jobs/followup-reminders. Schedule
// is configured in netlify.toml ([functions."followup-reminders"]).
//
// Reuses the same env vars as the Meta refresh job:
//   BACKEND_ORIGIN     e.g. https://lif-crm.onrender.com
//   META_REFRESH_TOKEN must equal the backend's META_VERIFY_TOKEN
export default async () => {
  const origin = process.env.BACKEND_ORIGIN;
  const token = process.env.META_REFRESH_TOKEN;
  if (!origin || !token) {
    return new Response("BACKEND_ORIGIN / META_REFRESH_TOKEN not configured", { status: 500 });
  }
  try {
    const res = await fetch(`${origin}/jobs/followup-reminders?token=${encodeURIComponent(token)}`, {
      method: "POST",
    });
    const body = await res.text();
    console.log(`followup-reminders -> ${res.status}: ${body}`);
    return new Response(body, { status: res.status });
  } catch (err) {
    console.error("followup-reminders failed", err);
    return new Response(String(err), { status: 502 });
  }
};
