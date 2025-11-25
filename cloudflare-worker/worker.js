export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const challengeCode = url.searchParams.get("challenge_code");

    // If eBay is validating the endpoint, it will send a GET with ?challenge_code=...
    if (request.method === "GET" && challengeCode) {
      // IMPORTANT: These must match exactly what you set in eBay
      const VERIFICATION_TOKEN = "UF_verify_token_for_ebay_marketplace_2025_123456";
      const ENDPOINT = "https://ebay-account-events.neal-24a.workers.dev"; // <-- paste your exact workers.dev URL here

      const encoder = new TextEncoder();
      const data = encoder.encode(challengeCode + VERIFICATION_TOKEN + ENDPOINT);

      // SHA-256 hash using Web Crypto API
      const hashBuffer = await crypto.subtle.digest("SHA-256", data);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      const hashHex = hashArray.map(b => b.toString(16).padStart(2, "0")).join("");

      const body = JSON.stringify({ challengeResponse: hashHex });

      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    // For any other request (POST notifications later, or manual visits), just return OK
    return new Response("OK", { status: 200 });
  }
};

};
