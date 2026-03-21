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
    protected RestContext m_Context;
    protected ref GM_RestCallback m_Callback;
    protected string m_sBaseURL;

    // Retry state
    protected int m_iConsecutiveErrors = 0;
    protected float m_fBackoffMultiplier = 1.0;
    protected bool m_bConnected = false;

    // Constants
    protected static const int MAX_CONSECUTIVE_ERRORS = 5;
    protected static const float MAX_BACKOFF = 120.0; // seconds

    void GM_RestClient(string baseURL = "http://localhost:8000")
    {
        m_sBaseURL = baseURL;
        m_Callback = new GM_RestCallback();
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

        m_Context = restApi.GetContext(m_sBaseURL);
        if (!m_Context)
        {
            Print("[GodMode:REST] Failed to create context for: " + m_sBaseURL, LogLevel.ERROR);
            return false;
        }

        m_Context.SetHeaders(
            "Content-Type: application/json\n" +
            "X-Game-Mode: god-mode\n" +
            "Accept: application/json"
        );

        m_bConnected = true;
        Print("[GodMode:REST] Initialized → " + m_sBaseURL);
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

        m_Context.POST(m_Callback, "/api/game-master", gameStateJSON);
        return true;
    }

    // Health check (async)
    void PingHealth()
    {
        if (!m_Context) return;
        m_Context.GET(m_Callback, "/health");
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
}
