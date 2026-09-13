import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Editor from "@monaco-editor/react";
import JSZip from "jszip";

const TERMINAL_URL = `${
  window.location.protocol === "https:" ? "wss:" : "ws:"
}//${window.location.host}/api/terminal`;

const WORKSPACE_STORAGE_KEY = "polyworkspace.workspace.v6";

const languageCards = [
  {
    id: "python",
    name: "Python",
    icon: "🐍",
    category: "programming",
    enabled: true,
    description: "Run Python multi-file projects",
  },
  {
    id: "java",
    name: "Java",
    icon: "☕",
    category: "programming",
    enabled: true,
    description: "Compile Java multi-file projects",
  },
  {
    id: "javascript",
    name: "JavaScript",
    icon: "JS",
    category: "web",
    enabled: false,
    description: "Coming soon",
  },
  {
    id: "cpp",
    name: "C++",
    icon: "C++",
    category: "programming",
    enabled: false,
    description: "Coming soon",
  },
  {
    id: "react",
    name: "React",
    icon: "⚛",
    category: "web",
    enabled: false,
    description: "Coming soon",
  },
  {
    id: "database",
    name: "Database",
    icon: "DB",
    category: "databases",
    enabled: false,
    description: "Coming soon",
  },
];

function getLanguageFromPath(path) {
  if (path.endsWith(".py")) {
    return "python";
  }

  if (path.endsWith(".java")) {
    return "java";
  }

  return "plaintext";
}

function getLanguageLabel(language) {
  if (language === "python") {
    return "Python";
  }

  if (language === "java") {
    return "Java";
  }

  return "Unknown";
}

function getEntrypoint(language, files) {
  if (language === "python") {
    const mainFile = files.find(
      (file) => file.path === "main.py",
    );

    return mainFile ? mainFile.path : files[0].path;
  }

  const mainFile = files.find(
    (file) => file.path === "Main.java",
  );

  if (mainFile) {
    return "Main";
  }

  return files[0].path
    .split("/")
    .pop()
    .replace(".java", "");
}

function loadSavedWorkspace() {
  try {
    const savedValue = localStorage.getItem(
      WORKSPACE_STORAGE_KEY,
    );

    if (!savedValue) {
      return null;
    }

    return JSON.parse(savedValue);
  } catch {
    return null;
  }
}

export default function App() {
  const [savedWorkspace] = useState(loadSavedWorkspace);

  const [screen, setScreen] = useState(
    savedWorkspace?.screen || "home",
  );

  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("popular");

  const [files, setFiles] = useState(
    Array.isArray(savedWorkspace?.files)
      ? savedWorkspace.files
      : [],
  );

  const [selectedPath, setSelectedPath] = useState(
    savedWorkspace?.selectedPath || "",
  );

  const [rightTab, setRightTab] = useState(
    savedWorkspace?.rightTab || "console",
  );

  const [leftPanel, setLeftPanel] = useState(
    savedWorkspace?.leftPanel || "files",
  );

  const [explorerVisible, setExplorerVisible] = useState(
    savedWorkspace?.explorerVisible ?? true,
  );

  const [isConsoleOpen, setIsConsoleOpen] = useState(
    savedWorkspace?.isConsoleOpen ?? false,
  );

  const [consoleHeight, setConsoleHeight] = useState(
    savedWorkspace?.consoleHeight || 220,
  );

  const [fileSearchQuery, setFileSearchQuery] = useState("");

  const [editorFontSize, setEditorFontSize] = useState(
    savedWorkspace?.editorFontSize || 22,
  );

  const [wordWrap, setWordWrap] = useState(
    savedWorkspace?.wordWrap ?? true,
  );

  const [theme, setTheme] = useState(
    savedWorkspace?.theme || "tokyo-night",
  );

  const [terminalOutput, setTerminalOutput] = useState(
    savedWorkspace?.terminalOutput || "",
  );

  const [terminalInput, setTerminalInput] = useState("");
  const [terminalState, setTerminalState] = useState("idle");

  const [terminalLanguage, setTerminalLanguage] = useState(
    savedWorkspace?.terminalLanguage || "",
  );

  const [isDownloading, setIsDownloading] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const terminalSocketRef = useRef(null);
  const terminalInputRef = useRef(null);
  const terminalOutputRef = useRef(null);
  const runQueueRef = useRef([]);
  const appContainerRef = useRef(null);

  const selectedFile = files.find(
    (file) => file.path === selectedPath,
  );

  const selectedLanguage = selectedFile
    ? getLanguageFromPath(selectedFile.path)
    : "plaintext";

  const pythonFiles = files.filter(
    (file) => getLanguageFromPath(file.path) === "python",
  );

  const javaFiles = files.filter(
    (file) => getLanguageFromPath(file.path) === "java",
  );

  const searchedFiles = files.filter((file) =>
    file.path
      .toLowerCase()
      .includes(fileSearchQuery.toLowerCase()),
  );

  const visibleCards = useMemo(() => {
    return languageCards.filter((card) => {
      const matchesSearch = card.name
        .toLowerCase()
        .includes(query.toLowerCase());

      const matchesCategory =
        category === "popular" ||
        card.category === category ||
        (category === "programming" && card.enabled);

      return matchesSearch && matchesCategory;
    });
  }, [query, category]);

  const isTerminalActive =
    terminalState === "connecting" ||
    terminalState === "running";

  useEffect(() => {
    const workspaceData = {
      screen,
      files,
      selectedPath,
      rightTab,
      leftPanel,
      explorerVisible,
      isConsoleOpen,
      consoleHeight,
      editorFontSize,
      wordWrap,
      theme,
      terminalOutput: terminalOutput.slice(-10000),
      terminalLanguage,
    };

    localStorage.setItem(
      WORKSPACE_STORAGE_KEY,
      JSON.stringify(workspaceData),
    );
  }, [
    screen,
    files,
    selectedPath,
    rightTab,
    leftPanel,
    explorerVisible,
    isConsoleOpen,
    consoleHeight,
    editorFontSize,
    wordWrap,
    theme,
    terminalOutput,
    terminalLanguage,
  ]);

  useEffect(() => {
    function handleFullscreenChange() {
      if (!document.fullscreenElement) {
        setIsFullscreen(false);
      }
    }
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
    };
  }, []);

  function toggleAppFullscreen() {
    if (!document.fullscreenElement) {
      appContainerRef.current?.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen().catch(() => {});
      setIsFullscreen(false);
    }
  }

  useEffect(() => {
    if (
      terminalState === "running" &&
      rightTab === "console" &&
      isConsoleOpen
    ) {
      terminalInputRef.current?.focus();
    }
  }, [terminalState, rightTab, isConsoleOpen]);

  useEffect(() => {
    if (terminalOutputRef.current) {
      terminalOutputRef.current.scrollTop =
        terminalOutputRef.current.scrollHeight;
    }
  }, [terminalOutput]);

  useEffect(() => {
    return () => {
      terminalSocketRef.current?.close();
    };
  }, []);

  function createProject(language) {
    const entryFile = language === "python"
      ? "main.py"
      : "Main.java";

    setFiles([
      {
        path: entryFile,
        content: "",
      },
    ]);

    setSelectedPath(entryFile);
    setTerminalOutput("");
    setTerminalInput("");
    setTerminalState("idle");
    setTerminalLanguage("");
    setExplorerVisible(true);
    setIsConsoleOpen(false);
    setLeftPanel("files");
    runQueueRef.current = [];
    setScreen("workspace");
  }

  function openWorkspace() {
    setScreen("workspace");
  }

  function selectLeftPanel(panelName) {
    if (leftPanel === panelName && explorerVisible) {
      setExplorerVisible(false);
      return;
    }

    setLeftPanel(panelName);
    setExplorerVisible(true);
  }

  function toggleTheme() {
    setTheme((currentTheme) =>
      currentTheme === "tokyo-night"
        ? "light"
        : "tokyo-night",
    );
  }

  function addFile() {
    const path = window.prompt(
      "Enter a file name: main.py, helper.py, Main.java, Student.java",
    );

    if (!path) {
      return;
    }

    const language = getLanguageFromPath(path);

    if (language !== "python" && language !== "java") {
      window.alert(
        "Currently, only .py and .java files are supported.",
      );
      return;
    }

    if (files.some((file) => file.path === path)) {
      window.alert("A file with that name already exists.");
      return;
    }

    setFiles((currentFiles) => [
      ...currentFiles,
      {
        path,
        content: "",
      },
    ]);

    setSelectedPath(path);
    setExplorerVisible(true);
    setLeftPanel("files");
  }

  function deleteSelectedFile() {
    if (!selectedPath) {
      return;
    }

    const shouldDelete = window.confirm(
      `Delete ${selectedPath}?`,
    );

    if (!shouldDelete) {
      return;
    }

    const remainingFiles = files.filter(
      (file) => file.path !== selectedPath,
    );

    setFiles(remainingFiles);
    setSelectedPath(remainingFiles[0]?.path || "");
  }

  function updateFileContent(content) {
    setFiles((currentFiles) =>
      currentFiles.map((file) =>
        file.path === selectedPath
          ? {
              ...file,
              content: content || "",
            }
          : file,
      ),
    );
  }

  function appendTerminalOutput(text) {
    setTerminalOutput((currentOutput) =>
      `${currentOutput}${text}`,
    );
  }

  function startConsoleResize(event) {
    event.preventDefault();

    const startY = event.clientY;
    const startHeight = consoleHeight;

    function handleMouseMove(moveEvent) {
      const heightChange = startY - moveEvent.clientY;

      const nextHeight = Math.max(
        120,
        Math.min(500, startHeight + heightChange),
      );

      setConsoleHeight(nextHeight);
    }

    function handleMouseUp() {
      window.removeEventListener(
        "mousemove",
        handleMouseMove,
      );

      window.removeEventListener(
        "mouseup",
        handleMouseUp,
      );
    }

    window.addEventListener(
      "mousemove",
      handleMouseMove,
    );

    window.addEventListener(
      "mouseup",
      handleMouseUp,
    );
  }

  async function downloadProject() {
    if (files.length === 0) {
      window.alert("Create at least one file before downloading.");
      return;
    }

    setIsDownloading(true);

    try {
      const zip = new JSZip();

      files.forEach((file) => {
        zip.file(file.path, file.content);
      });

      const zipFile = await zip.generateAsync({
        type: "blob",
      });

      const downloadUrl = URL.createObjectURL(zipFile);
      const link = document.createElement("a");

      link.href = downloadUrl;
      link.download = "polyworkspace-project.zip";

      document.body.appendChild(link);
      link.click();
      link.remove();

      URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      window.alert(
        `Could not download project: ${error.message}`,
      );
    } finally {
      setIsDownloading(false);
    }
  }

  function startNextQueuedProject() {
    const nextLanguage = runQueueRef.current.shift();

    if (nextLanguage) {
      startInteractiveProject(nextLanguage);
    }
  }

  function startInteractiveProject(language) {
    const projectFiles = language === "python"
      ? pythonFiles
      : javaFiles;

    if (projectFiles.length === 0) {
      appendTerminalOutput(
        `\nNo ${getLanguageLabel(language)} files exist in this workspace.\n`,
      );
      return;
    }

    terminalSocketRef.current?.close();

    setRightTab("console");
    setIsConsoleOpen(true);

    setTerminalOutput(
      `> Starting ${getLanguageLabel(language)} project...\n`,
    );

    setTerminalInput("");
    setTerminalLanguage(language);
    setTerminalState("connecting");

    const socket = new WebSocket(TERMINAL_URL);

    terminalSocketRef.current = socket;

    socket.onopen = () => {
      setTerminalState("running");

      socket.send(
        JSON.stringify({
          type: "start",
          request: {
            language,
            files: projectFiles,
            entrypoint: getEntrypoint(
              language,
              projectFiles,
            ),
          },
        }),
      );
    };

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);

      if (message.type === "started") {
        appendTerminalOutput(
          `[${getLanguageLabel(language)} terminal started]\n`,
        );
      }

      if (message.type === "output") {
        appendTerminalOutput(message.data);
      }

      if (message.type === "error") {
        appendTerminalOutput(
          `\nError: ${message.message}\n`,
        );

        setTerminalState("error");
      }

      if (message.type === "exit") {
        const status = message.success
          ? "completed successfully"
          : "finished with an error";

        appendTerminalOutput(
          `\n[${getLanguageLabel(language)} ${status}. Exit code: ${message.exit_code}]\n`,
        );

        setTerminalState("idle");
        terminalSocketRef.current = null;

        window.setTimeout(() => {
          startNextQueuedProject();
        }, 100);
      }
    };

    socket.onerror = () => {
      appendTerminalOutput(
        "\nError: Could not connect to the interactive terminal.\n",
      );

      setTerminalState("error");
    };

    socket.onclose = (event) => {
      if (terminalSocketRef.current !== socket) {
        return;
      }

      if (event.code !== 1000) {
        appendTerminalOutput(
          `\n[Terminal connection closed: ${event.code}]\n`,
        );
      }

      terminalSocketRef.current = null;
      setTerminalState("idle");
    };
  }

  function runCurrentFile() {
    if (!selectedFile) {
      return;
    }

    if (
      selectedLanguage !== "python" &&
      selectedLanguage !== "java"
    ) {
      window.alert("Select a Python or Java file first.");
      return;
    }

    runQueueRef.current = [];
    startInteractiveProject(selectedLanguage);
  }

  function runAllProjects() {
    const languagesToRun = [];

    if (pythonFiles.length > 0) {
      languagesToRun.push("python");
    }

    if (javaFiles.length > 0) {
      languagesToRun.push("java");
    }

    if (languagesToRun.length === 0) {
      window.alert(
        "Create a Python or Java file before running.",
      );
      return;
    }

    const firstLanguage = languagesToRun.shift();

    runQueueRef.current = languagesToRun;

    if (firstLanguage) {
      startInteractiveProject(firstLanguage);
    }
  }

  function sendTerminalInput(event) {
    event.preventDefault();

    const socket = terminalSocketRef.current;

    if (
      !socket ||
      socket.readyState !== WebSocket.OPEN ||
      terminalState !== "running"
    ) {
      return;
    }

    socket.send(
      JSON.stringify({
        type: "input",
        data: terminalInput,
      }),
    );

    appendTerminalOutput(`${terminalInput}\n`);
    setTerminalInput("");
  }

  function stopTerminal() {
    const socket = terminalSocketRef.current;

    if (
      socket &&
      socket.readyState === WebSocket.OPEN
    ) {
      socket.send(
        JSON.stringify({
          type: "stop",
        }),
      );

      appendTerminalOutput("\n[Stopping terminal...]\n");
    }
  }

  if (screen === "home") {
    return (
      <main className="home-page" ref={appContainerRef}>
        <nav className="navbar">
          <button
            className="brand"
            onClick={() => setScreen("home")}
          >
            <span className="brand-icon">&lt;/&gt;</span>
            SpCompiler
          </button>

          <button
            className="open-workspace"
            onClick={openWorkspace}
          >
            Open Workspace
          </button>
        </nav>

        <section className="hero">
          <p className="hero-label">
            SELF-HOSTED MULTI-LANGUAGE IDE
          </p>

          <h1>
            Code online with
            <span> SpCompiler.</span>
          </h1>

          <p>
            Create and run Python and Java projects in one
            workspace.
          </p>

          <input
            className="language-search"
            placeholder="Search Python, Java, JavaScript..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </section>

        <section
          className="language-section"
          id="languages"
        >
          <div className="tabs">
            {[
              ["popular", "Popular"],
              ["programming", "Programming"],
              ["web", "Web"],
              ["databases", "Databases"],
            ].map(([tabId, label]) => (
              <button
                key={tabId}
                className={
                  category === tabId
                    ? "tab active"
                    : "tab"
                }
                onClick={() => setCategory(tabId)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="language-grid">
            {visibleCards.map((card) => (
              <button
                className={
                  card.enabled
                    ? "language-card"
                    : "language-card disabled-card"
                }
                key={card.id}
                disabled={!card.enabled}
                onClick={() => createProject(card.id)}
              >
                <span className="language-icon">
                  {card.icon}
                </span>

                <span>
                  <strong>{card.name}</strong>
                  <small>{card.description}</small>
                </span>
              </button>
            ))}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main
      className="ide-shell"
      data-theme={theme}
      ref={appContainerRef}
    >
      {isFullscreen && (
        <button
          className="fullscreen-exit-btn"
          onClick={toggleAppFullscreen}
          title="Exit Fullscreen"
        >
          ✕
        </button>
      )}

      <header className="ide-header">
        <button
          className="ide-brand"
          onClick={() => setScreen("home")}
          title="Return to language selection"
        >
          <span>&lt;/&gt;</span>
          SpCompiler
        </button>

        <div className="top-actions">
          <span className="auto-language">
            {getLanguageLabel(selectedLanguage)}
          </span>

          <button
            className="compact-button run-button"
            disabled={!selectedFile}
            onClick={runCurrentFile}
            title="Run selected file"
          >
            ▶ Run
          </button>

          <button
            className="compact-button"
            disabled={
              pythonFiles.length === 0 &&
              javaFiles.length === 0
            }
            onClick={runAllProjects}
            title="Run Python and Java projects"
          >
            Run All
          </button>

          <button
            className="compact-button download-button"
            disabled={
              files.length === 0 ||
              isDownloading
            }
            onClick={downloadProject}
            title="Download project as ZIP"
          >
            {isDownloading ? "..." : "⇩"}
          </button>

          <button
            className="theme-toggle"
            onClick={toggleTheme}
            title={
              theme === "tokyo-night"
                ? "Switch to light theme"
                : "Switch to Tokyo Night theme"
            }
          >
            {theme === "tokyo-night" ? "☀" : "🌙"}
          </button>

          <button
            className="compact-button fullscreen-toggle-btn"
            onClick={toggleAppFullscreen}
            title="Toggle Fullscreen Workspace"
          >
            ⛶
          </button>
        </div>

        <div className="profile-circle">S</div>
      </header>

      <section
        className="ide-layout"
        style={{
          "--explorer-width": explorerVisible
            ? "250px"
            : "0px",
          "--console-height": isConsoleOpen
            ? `${consoleHeight}px`
            : "0px",
        }}
      >
        <aside className="activity-bar">
          <div className="activity-pc-logo" title="SpCompiler IDE">
            <span>PC</span>
          </div>

          <button
            className={
              leftPanel === "files" && explorerVisible
                ? "activity-icon active"
                : "activity-icon"
            }
            onClick={() => selectLeftPanel("files")}
            title="Project Files (Folder)"
          >
            📁
          </button>

          <button
            className={
              leftPanel === "search" && explorerVisible
                ? "activity-icon active"
                : "activity-icon"
            }
            onClick={() => selectLeftPanel("search")}
            title="Search Files"
          >
            🔲
          </button>

          <button
            className="activity-icon"
            onClick={() => selectLeftPanel("files")}
            title="More Options"
          >
            ···
          </button>

          <div className="activity-divider" />

          <button
            className="activity-icon"
            onClick={() => startInteractiveProject("python")}
            title="Python Terminal"
          >
            🐍
          </button>

          <button
            className="activity-icon"
            onClick={() => startInteractiveProject("java")}
            title="Java Environment"
          >
            📚
          </button>

          <button
            className="activity-icon"
            onClick={runCurrentFile}
            title="Execute Run"
          >
            ▶
          </button>

          <button
            className="activity-icon"
            onClick={() => {
              setIsConsoleOpen(true);
              setRightTab("console");
            }}
            title="Terminal Console Window"
          >
            💻
          </button>

          <button
            className={
              leftPanel === "history" && explorerVisible
                ? "activity-icon active"
                : "activity-icon"
            }
            onClick={() => selectLeftPanel("history")}
            title="Warnings & Diagnostics"
          >
            ⚠️
          </button>

          <button
            className={
              leftPanel === "settings" && explorerVisible
                ? "activity-icon active bottom"
                : "activity-icon bottom"
            }
            onClick={() => selectLeftPanel("settings")}
            title="Settings"
          >
            ⚙
          </button>
        </aside>

        <aside
          className={
            explorerVisible
              ? "explorer"
              : "explorer explorer-hidden"
          }
        >
          {leftPanel === "files" && (
            <>
              <div className="explorer-heading">
                <span>PROJECT FILES</span>

                <div className="explorer-actions">
                  <button
                    onClick={addFile}
                    title="Create file"
                  >
                    ＋
                  </button>

                  <button
                    onClick={() => setExplorerVisible(false)}
                    title="Hide explorer"
                  >
                    ←
                  </button>
                </div>
              </div>

              <div className="file-tree">
                {files.length === 0 && (
                  <p className="empty-tree">
                    Click ＋ to create a Python or Java file.
                  </p>
                )}

                {files.map((file) => (
                  <div
                    className={
                      file.path === selectedPath
                        ? "tree-file selected"
                        : "tree-file"
                    }
                    key={file.path}
                    onClick={() => setSelectedPath(file.path)}
                  >
                    <span>{file.path}</span>

                    <div className="tree-file-actions">
                      <small>
                        {getLanguageLabel(
                          getLanguageFromPath(file.path),
                        )}
                      </small>
                      <button
                        className="tree-delete-btn"
                        title="Delete file"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm(`Delete ${file.path}?`)) {
                            const remaining = files.filter(f => f.path !== file.path);
                            setFiles(remaining);
                            if (selectedPath === file.path) {
                              setSelectedPath(remaining[0]?.path || "");
                            }
                          }
                        }}
                      >
                        ×
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {leftPanel === "search" && (
            <div className="search-panel">
              <div className="explorer-heading">
                <span>SEARCH FILES</span>

                <button
                  onClick={() => setExplorerVisible(false)}
                  title="Hide explorer"
                >
                  ←
                </button>
              </div>

              <input
                className="file-search-input"
                placeholder="Search file name..."
                value={fileSearchQuery}
                onChange={(event) =>
                  setFileSearchQuery(event.target.value)
                }
              />

              <div className="search-results">
                {searchedFiles.map((file) => (
                  <button
                    className="search-result"
                    key={file.path}
                    onClick={() => {
                      setSelectedPath(file.path);
                      setLeftPanel("files");
                    }}
                  >
                    <strong>{file.path}</strong>

                    <small>
                      {getLanguageLabel(
                        getLanguageFromPath(file.path),
                      )}
                    </small>
                  </button>
                ))}

                {searchedFiles.length === 0 && (
                  <p className="empty-tree">
                    No matching files found.
                  </p>
                )}
              </div>
            </div>
          )}

          {leftPanel === "history" && (
            <div className="side-message">
              <div className="explorer-heading">
                <span>LOCAL HISTORY</span>

                <button
                  onClick={() => setExplorerVisible(false)}
                  title="Hide explorer"
                >
                  ←
                </button>
              </div>

              <h3>Browser saved</h3>

              <p>
                Your project files are automatically saved in this
                browser storage securely.
              </p>

              <p>
                Refreshing this page keeps your active current session files.
              </p>
            </div>
          )}

          {leftPanel === "settings" && (
            <div className="settings-panel">
              <div className="explorer-heading">
                <span>EDITOR SETTINGS</span>

                <button
                  onClick={() => setExplorerVisible(false)}
                  title="Hide explorer"
                >
                  ←
                </button>
              </div>

              <label className="setting-label">
                Font size: {editorFontSize}px

                <input
                  type="range"
                  min="12"
                  max="28"
                  value={editorFontSize}
                  onChange={(event) =>
                    setEditorFontSize(
                      Number(event.target.value),
                    )
                  }
                />
              </label>

              <label className="checkbox-setting">
                <input
                  type="checkbox"
                  checked={wordWrap}
                  onChange={(event) =>
                    setWordWrap(event.target.checked)
                  }
                />

                <span>Enable word wrap</span>
              </label>

              <label className="checkbox-setting">
                <input
                  type="checkbox"
                  checked={theme === "tokyo-night"}
                  onChange={toggleTheme}
                />

                <span>Use Tokyo Night theme</span>
              </label>

              <button
                className="clear-console-btn"
                onClick={() => setTerminalOutput("")}
              >
                Clear console output
              </button>
            </div>
          )}
        </aside>

        <section className="editor-area">
          <div className="editor-tabs">
            {selectedFile ? (
              <div className="editor-tab">
                <span>{selectedFile.path}</span>

                <button
                  onClick={deleteSelectedFile}
                  title="Delete file"
                >
                  ×
                </button>
              </div>
            ) : (
              <div className="editor-tab muted">
                No file open
              </div>
            )}

            <button
              className="new-tab"
              onClick={addFile}
              title="Create file"
            >
              ＋
            </button>
          </div>

          <div className="editor-content">
            {selectedFile ? (
              <Editor
                height="100%"
                language={getLanguageFromPath(selectedFile.path)}
                value={selectedFile.content}
                onChange={updateFileContent}
                theme={
                  theme === "tokyo-night"
                    ? "vs-dark"
                    : "vs"
                }
                options={{
                  fontSize: editorFontSize,
                  minimap: {
                    enabled: false,
                  },
                  automaticLayout: true,
                  wordWrap: wordWrap ? "on" : "off",
                  padding: {
                    top: 16,
                  },
                  readOnly: isTerminalActive,
                }}
              />
            ) : (
              <div className="empty-editor">
                Create a file from the left panel to start coding.
              </div>
            )}
          </div>
        </section>

        {isConsoleOpen && (
          <>
            <div
              className="console-resizer"
              onMouseDown={startConsoleResize}
              title="Drag to resize console height"
            />

            <aside className="console-panel">
              <div className="console-tabs">
                <div className="console-tabs-left">
                  <button
                    className={
                      rightTab === "console"
                        ? "console-tab active"
                        : "console-tab"
                    }
                    onClick={() => setRightTab("console")}
                  >
                    Console
                  </button>

                  <button
                    className={
                      rightTab === "io"
                        ? "console-tab active"
                        : "console-tab"
                    }
                    onClick={() => setRightTab("io")}
                  >
                    Help
                  </button>
                </div>

                <div className="console-tabs-right">
                  <button
                    className="console-close-btn"
                    onClick={() => setIsConsoleOpen(false)}
                    title="Close console"
                  >
                    ×
                  </button>
                </div>
              </div>

              {rightTab === "console" && (
                <div className="console-content">
                  <section className="live-terminal">
                    <div className="terminal-heading">
                      <h3>
                        {terminalLanguage
                          ? `${getLanguageLabel(terminalLanguage)} Terminal`
                          : "Live Console"}
                      </h3>

                      <div className="terminal-heading-actions">
                        <span
                          className={
                            isTerminalActive
                              ? "terminal-status active"
                              : "terminal-status"
                          }
                        >
                          {isTerminalActive ? "Running" : "Ready"}
                        </span>

                        {isTerminalActive && (
                          <button
                            className="stop-terminal-sm"
                            onClick={stopTerminal}
                          >
                            Stop
                          </button>
                        )}
                      </div>
                    </div>

                    <pre
                      className="terminal-output"
                      ref={terminalOutputRef}
                    >
                      {terminalOutput ||
                        "Click Run to start your project."}
                    </pre>

                    <form
                      className="terminal-input-row"
                      onSubmit={sendTerminalInput}
                    >
                      <span>›</span>

                      <input
                        ref={terminalInputRef}
                        value={terminalInput}
                        onChange={(event) =>
                          setTerminalInput(event.target.value)
                        }
                        disabled={!isTerminalActive}
                        placeholder={
                          isTerminalActive
                            ? "Type input and press Enter"
                            : "Run a program to enable input"
                        }
                      />

                      <button
                        type="submit"
                        disabled={!isTerminalActive}
                      >
                        Enter
                      </button>
                    </form>
                  </section>
                </div>
              )}

              {rightTab === "io" && (
                <div className="io-content">
                  <h3>Interactive Terminal Help</h3>

                  <p>
                    Click Run. If your program uses input(), the prompt
                    appears in Console.
                  </p>

                  <p>
                    Type your answer in the input field and press Enter.
                  </p>

                  <p>
                    Drag the top edge of this panel to resize its height.
                  </p>
                </div>
              )}
            </aside>
          </>
        )}
      </section>

      <footer className="status-bar">
        <span>
          {isTerminalActive
            ? "● Program running"
            : "● Ready"}
        </span>

        <span>
          {theme === "tokyo-night"
            ? "Tokyo Night"
            : "Light Theme"}
        </span>

        <span>{getLanguageLabel(selectedLanguage)}</span>
      </footer>
    </main>
  );
}