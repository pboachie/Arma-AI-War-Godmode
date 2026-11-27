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
    protected static const string REST_CLIENT_REVISION = "GM_REST_RETRY_20261127";
    protected static const string GAME_MASTER_ENDPOINT = "api/game-master";
    protected static const string GAME_KEY_PATH = "$profile:godmode_api_key.txt";
    protected static const string GAME_KEY_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
    protected static const string HEADER_DEFINITION = "Content-Type,application/json,Accept,application/json,Connection,keep-alive";

    protected RestContext m_Context;
    protected ref GM_RestCallback m_Callback;           // Template: holds ScriptInvoker wiring
    protected ref GM_RestCallback m_ActiveCallback;     // Per-request: prevents stale-callback warnings
    protected string m_sBaseURL;
    protected string m_sGameAPIKey;

    // Retry state
    protected int m_iConsecutiveErrors = 0;
    protected int m_iLastFailureTick = 0;
    protected float m_fBackoffMultiplier = 1.0;
    protected bool m_bConnected = false;
    protected bool m_bRequestPending = false;

    // Constants
    protected static const int MAX_CONSECUTIVE_ERRORS = 5;
    protected static const float MAX_BACKOFF = 120.0; // seconds

    void GM_RestClient(string baseURL = "http://127.0.0.1:8080")
    {
        m_sBaseURL = NormalizeBaseURL(baseURL);
        m_Callback = new GM_RestCallback();
    }

    protected string NormalizeBaseURL(string baseURL)
    {
        string normalized = baseURL.Trim();
        if (normalized == "")
            return "http://127.0.0.1:8080";

        if (!normalized.Contains("://"))
            normalized = "http://" + normalized;

        // Strip trailing slashes
        while (normalized.Length() > 0 && normalized.Substring(normalized.Length() - 1, 1) == "/")
            normalized = normalized.Substring(0, normalized.Length() - 1);

        if (normalized.Contains("?") || normalized.Contains("#") || normalized.Contains("@") || normalized.Contains("\\"))
            return "";

        // Preserve old local scenario settings without DNS resolution.
        if (normalized == "http://localhost")
            return "http://127.0.0.1";
        if (normalized.StartsWith("http://localhost:"))
            normalized = "http://127.0.0.1:" + normalized.Substring(17, normalized.Length() - 17);

        if (normalized.StartsWith("https://") && normalized.Length() > 8)
            return normalized;
        if (normalized == "http://127.0.0.1")
            return normalized;
        if (!normalized.StartsWith("http://127.0.0.1:"))
            return "";

        string port = normalized.Substring(17, normalized.Length() - 17);
        if (port.Length() == 0 || port.Length() > 5)
            return "";
        for (int i = 0; i < port.Length(); i++)
        {
            if (!port.IsDigitAt(i))
                return "";
        }
        int portNumber = port.ToInt();
        if (portNumber < 1 || portNumber > 65535)
            return "";

        return normalized;
    }

    protected bool LoadGameAPIKey()
    {
        FileHandle file = FileIO.OpenFile(GAME_KEY_PATH, FileMode.READ);
        if (!file)
            return false;

        // Read at most 128 URL-safe bytes and an optional LF/CRLF.
        int size = file.GetLength();
        if (size < 32 || size > 130)
        {
            file.Close();
            return false;
        }
        string key;
        int read = file.Read(key, size);
        file.Close();
        if (read != size || key.Length() != size)
            return false;
        if (key.EndsWith("\n"))
        {
            key = key.Substring(0, key.Length() - 1);
            if (key.EndsWith("\r"))
                key = key.Substring(0, key.Length() - 1);
        }
        if (key.Length() < 32 || key.Length() > 128)
            return false;
        for (int i = 0; i < key.Length(); i++)
        {
            if (!GAME_KEY_CHARS.Contains(key.Substring(i, 1)))
                return false;
        }
        m_sGameAPIKey = key;
        return true;
    }

    // Re-apply headers before every request.  Enfusion's REST engine resets
    // the Connection header to "Upgrade, HTTP2-Settings" after each response,
    // which triggers h2c negotiation that uvicorn / httptools will reject.
    // Calling SetHeaders before each POST ensures "Connection: keep-alive".
    protected bool ApplyHeaders()
    {
        if (!m_Context || m_sGameAPIKey == "")
            return false;

        if (!m_Context.SetHeaders(HEADER_DEFINITION + ",X-API-Key," + m_sGameAPIKey))
        {
            Print("[GodMode:REST] Failed to apply headers", LogLevel.WARNING);
            return false;
        }
        return true;
    }

    protected string GetContextURL()
    {
        if (m_sBaseURL.Substring(m_sBaseURL.Length() - 1, 1) == "/")
            return m_sBaseURL;

        return m_sBaseURL + "/";
    }

    // Call once after world loads
    bool Init()
    {
        if (!Replication.IsServer() || RplSession.Mode() != RplMode.Dedicated)
            return false;
        if (m_sBaseURL == "")
        {
            Print("[GodMode:REST] Middleware requires an HTTPS URL or local HTTP loopback", LogLevel.WARNING);
            return false;
        }
        if (!LoadGameAPIKey())
        {
            Print("[GodMode:REST] Missing or invalid server profile game key file", LogLevel.WARNING);
            return false;
        }

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
            return false;

        m_bConnected = true;
        Print("[GodMode:REST] " + REST_CLIENT_REVISION + " context=" + contextURL + " endpoint=" + GAME_MASTER_ENDPOINT);
        Print("[GodMode:REST] Initialized -> " + m_sBaseURL);
        return true;
    }

    // Send SITREP to middleware (async, non-blocking)
    bool SendSITREP(string gameStateJSON)
    {
        if (!Replication.IsServer() || RplSession.Mode() != RplMode.Dedicated || !m_Context || !IsConnected())
        {
            Print("[GodMode:REST] Not connected, skipping SITREP", LogLevel.WARNING);
            return false;
        }

        // Re-apply headers every request — Enfusion resets Connection to h2c
        if (!ApplyHeaders())
        {
            OnError();
            return false;
        }

        // Fresh callback per request prevents "Function was not set for event"
        // engine warnings caused by stale callback state from prior requests.
        m_ActiveCallback = new GM_RestCallback();
        m_ActiveCallback.m_OnResponse = m_Callback.m_OnResponse;
        m_ActiveCallback.m_OnError    = m_Callback.m_OnError;

        // Mark even a half-open probe pending until the REST callback finishes.
        m_bRequestPending = true;
        Print("[GodMode:REST] POST " + GAME_MASTER_ENDPOINT + " bytes=" + gameStateJSON.Length());
        m_Context.POST(m_ActiveCallback, GAME_MASTER_ENDPOINT, gameStateJSON);
        return true;
    }

    // Call on successful response
    void OnSuccess()
    {
        m_bRequestPending = false;
        m_iConsecutiveErrors = 0;
        m_fBackoffMultiplier = 1.0;
        m_bConnected = true;
    }

    // Call on error response
    void OnError()
    {
        m_bRequestPending = false;
        m_iConsecutiveErrors++;
        m_iLastFailureTick = System.GetTickCount();
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
    bool IsConnected()
    {
        if (!m_bConnected || m_bRequestPending)
            return false;
        if (m_iConsecutiveErrors < MAX_CONSECUTIVE_ERRORS)
            return true;
        int elapsed = System.GetTickCount() - m_iLastFailureTick;
        return elapsed >= m_fBackoffMultiplier * 1000.0;
    }
    int GetConsecutiveErrors() { return m_iConsecutiveErrors; }
    float GetBackoffMultiplier() { return m_fBackoffMultiplier; }
    string GetBaseURL() { return m_sBaseURL; }
    string GetRevisionTag() { return REST_CLIENT_REVISION; }
}
