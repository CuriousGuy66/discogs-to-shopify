export default {
  async fetch(request, env, ctx) {
    // eBay sends { "challenge": "xxxx" } in the POST body.
    if (request.method === "POST") {
      let bodyText = await request.text();
      try {
        const data = JSON.parse(bodyText || "{}");

        if (data.challenge) {
          return new Response(
            JSON.stringify({ challenge: data.challenge }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }
          );
        }
      } catch (err) {
        // Fall through to generic 200 OK
      }
    }

    // Default: respond 200 OK for any GET or unrecognized POST
    return new Response("OK", { status: 200 });
  }
};
