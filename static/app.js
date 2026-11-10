import { initPrompts } from "./js/prompts.js";
import { initProjectContext } from "./js/project-context.js";
import { initRepositoryViewer } from "./js/repository-viewer.js";
import { loadConfig, refreshStatus } from "./js/header.js";
import { initProviders } from "./js/providers.js";
import { initProjectSettings } from "./js/project-settings.js";
import { initProjectSelectors } from "./js/project-selectors.js";
import { initTaskBoard } from "./js/task-board.js";
import { initTaskDialog } from "./js/task-dialog.js";
import { initTaskDetail } from "./js/task-detail.js";
import { initNotifications } from "./js/notifications.js";
import { initStatistics } from "./js/statistics.js";
import { initChat } from "./js/chat.js";
import { initSettings } from "./js/settings.js";

const { loadAgentPrompts } = initPrompts();
initProjectContext();
const { openRepositoryView, openDocumentationFile } = initRepositoryViewer();
const featureContext = {
  openRepositoryView,
};
const projectSelectorsApi = initProjectSelectors(featureContext);
featureContext.renderActiveProjectSelect = projectSelectorsApi.renderActiveProjectSelect;
featureContext.renderActiveSubprojectSelect = projectSelectorsApi.renderActiveSubprojectSelect;
const taskBoardApi = initTaskBoard(featureContext);
const { refreshBoard, renderTasks } = taskBoardApi;
featureContext.refreshBoard = refreshBoard;
featureContext.renderTasks = renderTasks;
const chatApi = initChat({ refreshBoard });
const { loadChat } = chatApi;
featureContext.loadChat = loadChat;
const { openTaskDialog } = initTaskDialog({ refreshBoard });
const providersApi = initProviders({ refreshStatus, refreshBoard });
const { reloadSettingsData, setConnectionsSuccess, populateProviderSelects } = providersApi;
const { loadProjectsIntoSettings } = initProjectSettings({ refreshBoard });
const { openTaskDetail, renderTaskDetailContent } = initTaskDetail({
  refreshBoard,
  loadChat,
  populateProviderSelects,
  openTaskDialog,
});
featureContext.openTaskDetail = openTaskDetail;
featureContext.renderTaskDetailContent = renderTaskDetailContent;
const notificationsApi = initNotifications({ refreshBoard, openTaskDetail });
featureContext.renderNotifications = notificationsApi.renderNotifications;
const statisticsApi = initStatistics({ refreshBoard });
featureContext.renderStatistics = statisticsApi.renderStatistics;

initSettings({
  loadProjectsIntoSettings,
  reloadSettingsData,
  setConnectionsSuccess,
  loadAgentPrompts,
  openDocumentationFile,
});

void loadConfig(providersApi);
void refreshBoard();
void loadChat();
void refreshStatus();
setInterval(() => void refreshBoard(), 5000);
setInterval(() => void refreshStatus(), 2000);
