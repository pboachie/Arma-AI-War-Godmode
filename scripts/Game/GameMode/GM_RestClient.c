// GM_RestClient.c — Production REST API wrapper with retry logic
// Handles all HTTP communication between the game and the FastAPI middleware.

class GM_RestCallback : RestCallback
{
    ref ScriptInvoker m_OnResponse = new ScriptInvoker();
    ref ScriptInvoker m_OnError = new ScriptInvoker();

    override void OnSuccess(string data, int dataSize)
    {
        if (data == "" || dataSize == 0)
        {
            Print("[GodMode:REST] Empty response received", LogLevel.WARNING);
            m_OnError.Invoke(-2);
            return;
        }
        m_OnResponse.Invoke(data);
    }

    override void OnError(int errorCode)
    {
        Print("[GodMode:REST] HTTP error: " + errorCode, LogLevel.ERROR);
        m_OnError.Invoke(errorCode);
    }

    override void OnTimeout()
    {
        Print("[GodMode:REST] Request timed out", LogLevel.WARNING);
        m_OnError.Invoke(-1);
    }
}

class GM_RestClient
{
    protected static const string REST_CLIENT_REVISION = "GM_REST_REV_20260329_A";
    protected static const string GAME_MASTER_ENDPOINT = "api/game-master";
    protected static const string HEALTH_ENDPOINT = "health";
    protected static const string HEADER_DEFINITION = "Content-Type,application/json,Accept,application/json,Connection,keep-alive";

    protected RestContext m_Context;
    protected ref GM_RestCallback m_Callback;           // Template: holds ScriptInvoker wiring
    protected ref GM_RestCallback m_ActiveCallback;     // Per-request: prevents stale-callback warnings
    protected string m_sBaseURL;

    // Retry state
    protected int m_iConsecutiveErrors = 0;
    protected float m_fBackoffMultiplier = 1.0;
    protected bool m_bConnected = false;
    protected bool m_bPayloadPreviewLogged = false;

    // Constants
    protected static const int MAX_CONSECUTIVE_ERRORS = 5;
    protected static const float MAX_BACKOFF = 120.0; // seconds

    void GM_RestClient(string baseURL = "http://localhost:8080")
    {
        m_sBaseURL = NormalizeBaseURL(baseURL);
        m_Callback = new GM_RestCallback();
    }

    protected string NormalizeBaseURL(string baseURL)
    {
        string normalized = baseURL.Trim();
        if (normalized == "")
            return "http://localhost:8080";

        if (!normalized.Contains("://"))
            normalized = "http://" + normalized;

        // Strip trailing slashes
        while (normalized.Length() > 0 && normalized.Substring(normalized.Length() - 1, 1) == "/")
            normalized = normalized.Substring(0, normalized.Length() - 1);

        if (normalized == "")
            return "http://localhost:8080";

        return normalized;
    }

    // Re-apply headers before every request.  Enfusion's REST engine resets
    // the Connection header to "Upgrade, HTTP2-Settings" after each response,
    // which triggers h2c negotiation that uvicorn / httptools will reject.
    // Calling SetHeaders before each POST ensures "Connection: keep-alive".
    protected bool ApplyHeaders()
    {
        if (!m_Context)
            return false;

        if (!m_Context.SetHeaders(HEADER_DEFINITION))
        {
            Print("[GodMode:REST] Failed to apply headers", LogLevel.WARNING);
            return false;
        }
        return true;
    }

    protected string GetContextURL()
    {
        if (m_sBaseURL == "")
            return "http://localhost:8080/";

        if (m_sBaseURL.Substring(m_sBaseURL.Length() - 1, 1) == "/")
            return m_sBaseURL;

        return m_sBaseURL + "/";
    }

    // Call once after world loads
    bool Init()
    {
        RestApi restApi = GetGame().GetRestApi();
        if (!restApi)
        {
            Print("[GodMode:REST] RestApi not available!", LogLevel.ERROR);
            return false;
        }

        string contextURL = GetContextURL();
        m_Context = restApi.GetContext(contextURL);
        if (!m_Context)
        {
            Print("[GodMode:REST] Failed to create context for: " + contextURL, LogLevel.ERROR);
            return false;
        }

        if (!ApplyHeaders())
            Print("[GodMode:REST] Continuing without explicit default headers", LogLevel.WARNING);

        m_bConnected = true;
        Print("[GodMode:REST] " + REST_CLIENT_REVISION + " context=" + contextURL + " endpoint=" + GAME_MASTER_ENDPOINT);
        Print("[GodMode:REST] Initialized -> " + m_sBaseURL);
        return true;
    }

    // Send SITREP to middleware (async, non-blocking)
    bool SendSITREP(string gameStateJSON)
    {
        if (!m_Context || !m_bConnected)
        {
            Print("[GodMode:REST] Not connected, skipping SITREP", LogLevel.WARNING);
            return false;
        }

        // Circuit breaker: if too many errors, back off
        if (m_iConsecutiveErrors >= MAX_CONSECUTIVE_ERRORS)
        {
            Print("[GodMode:REST] Circuit breaker open (" + m_iConsecutiveErrors + " errors). Backing off.", LogLevel.WARNING);
            return false;
        }

        // Re-apply headers every request — Enfusion resets Connection to h2c
        ApplyHeaders();

        if (!m_bPayloadPreviewLogged)
        {
            string payloadPreview = gameStateJSON;
            if (payloadPreview.Length() > 240)
                payloadPreview = payloadPreview.Substring(0, 240);

            payloadPreview.Replace("\n", " ");
            payloadPreview.Replace("\r", " ");
            Print("[GodMode:REST] Payload preview " + payloadPreview);
            m_bPayloadPreviewLogged = true;
        }

        // Fresh callback per request prevents "Function was not set for event"
        // engine warnings caused by stale callback state from prior requests.
        m_ActiveCallback = new GM_RestCallback();
        m_ActiveCallback.m_OnResponse = m_Callback.m_OnResponse;
        m_ActiveCallback.m_OnError    = m_Callback.m_OnError;

        Print("[GodMode:REST] POST " + GAME_MASTER_ENDPOINT + " bytes=" + gameStateJSON.Length());
        m_Context.POST(m_ActiveCallback, GAME_MASTER_ENDPOINT, gameStateJSON);
        return true;
    }

    // Health check (async)
    void PingHealth()
    {
        if (!m_Context) return;
        ApplyHeaders();
        ref GM_RestCallback hcb = new GM_RestCallback();
        m_Context.GET(hcb, HEALTH_ENDPOINT);
    }

    // Call on successful response
    void OnSuccess()
    {
        m_iConsecutiveErrors = 0;
        m_fBackoffMultiplier = 1.0;
        m_bConnected = true;
    }

    // Call on error response
    void OnError()
    {
        m_iConsecutiveErrors++;
        m_fBackoffMultiplier = Math.Min(m_fBackoffMultiplier * 2.0, MAX_BACKOFF);
    }

    // Reset circuit breaker (e.g., after manual reconnect)
    void ResetCircuitBreaker()
    {
        m_iConsecutiveErrors = 0;
        m_fBackoffMultiplier = 1.0;
        m_bConnected = true;
        Print("[GodMode:REST] Circuit breaker reset");
    }

    // Getters
    GM_RestCallback GetCallback() { return m_Callback; }
    bool IsConnected() { return m_bConnected && m_iConsecutiveErrors < MAX_CONSECUTIVE_ERRORS; }
    int GetConsecutiveErrors() { return m_iConsecutiveErrors; }
    float GetBackoffMultiplier() { return m_fBackoffMultiplier; }
    string GetBaseURL() { return m_sBaseURL; }
    string GetRevisionTag() { return REST_CLIENT_REVISION; }
}
