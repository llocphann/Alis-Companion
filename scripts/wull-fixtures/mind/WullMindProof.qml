import QtQuick
import QtTest
import Quickshell
import qs.services
import "../../../optional/hadanion/services"
import qs.modules.common
import qs.modules.settings
import qs.optional.hadanion.modules.abyss.companion
Window {
    id:root;visible:true;width:900;height:750;color:"#061521"
    Component.onCompleted: Quickshell.watchFiles=false
    property int outsideClicks: 0
    PointHandler {
        parent:root.contentItem
        onActiveChanged: if(active && !cloud.containsScenePoint(point.scenePosition)) root.outsideClicks++
    }
    function named(item,name) {
        if(item.objectName===name)return item
        for(const child of item.data ?? item.children ?? []){const match=named(child,name);if(match)return match}
        return null
    }
    AbyssCompanion {id:actor;x:570;y:550;reveal:1;motionEnabled:false;upright:true;interactive:true}
    WullTalkCloud {id:cloud;actor:actor;outputWidth:root.width;outputHeight:root.height;allowed:true}
    WullCloudActions {id:actions;actor:actor;outputWidth:root.width;outputHeight:root.height;allowed:true}
    CompanionConfig {id:settings;visible:false;width:800;height:700;activeSection:"overview"}
    TestCase {
        id:input;when:false;optional:true
        function check(value,message):void {if(!value)throw new Error(message)}
        function runChecks():void {
            try {
                tryCompare(Config,"ready",true,4000)
                Ai._initialized=true
                Ai.addModel("tiny:local",{name:"Tiny fixture",model:"tiny:local",local:true,
                    local_verified:true,local_fingerprint:Quickshell.env("WULL_TEST_LOCAL_FINGERPRINT"),provider_id:"lmstudio",
                    requires_key:false,api_format:"openai",endpoint:Quickshell.env("WULL_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"unsupported"}})
                Ai.modelList=Object.keys(Ai.models);Ai.currentModelId="tiny:local"
                settings.activeSection="ai"
                Config.setNestedValues({"abyss.companionMind.proactive":"manual",
                    "abyss.companionMind.model":"tiny:local","abyss.companionMind.obsidianEnabled":false})
                wait(80)
                const cadences=WullMind.proactiveProfiles
                check(WullMind.proactive==="manual" && !WullMind.proactiveIdleEnabled
                    && cadences.rare.checkInMs>cadences.occasional.checkInMs
                    && cadences.occasional.checkInMs>cadences.regular.checkInMs
                    && cadences.regular.checkInMs>cadences.often.checkInMs
                    && cadences.rare.playfulMs>cadences.occasional.playfulMs
                    && cadences.occasional.playfulMs>cadences.regular.playfulMs
                    && cadences.regular.playfulMs>cadences.often.playfulMs,
                    "proactive cadence profiles are not ordered or manual-safe")
                const syntheticNow=new Date(2026,9,5,9,0,0)
                check(WullMind.reminderRows(syntheticNow,[],[]).length===0,"reminder sources default to private")
                Config.setNestedValues({"abyss.companionMind.todoRemindersEnabled":true,
                    "abyss.companionMind.calendarRemindersEnabled":true,
                    "abyss.companionMind.journalRemindersEnabled":true})
                WullMind.journal=({date:"2026-10-05",schedule:[{start:545,title:"Obsolete Obsidian schedule",kind:"schedule"}],mood:"",energy:"",journalPath:""})
                const reminderRows=WullMind.reminderRows(syntheticNow,
                    [{done:false,sourceDate:"2026-10-05",startTime:"09:05",content:"Todo reminder"}],
                    [{id:"evt_planner_1",title:"Agenda reminder",recurrence:"none",
                      occurrences:[{startDate:new Date(2026,9,5,9,7,0).toISOString(),endDate:new Date(2026,9,5,9,37,0).toISOString(),notified:false}]},
                     {id:"evt_planner_weekly",title:"Weekly standup",recurrence:"weekly",
                      occurrences:[{startDate:new Date(2026,8,28,9,9,0).toISOString(),endDate:new Date(2026,8,28,9,39,0).toISOString(),notified:false}]}])
                check(reminderRows.some(row=>row.kind==="task" && row.title==="Todo reminder" && row.key==="todo:545")
                    && reminderRows.some(row=>row.kind==="agenda" && row.title==="Agenda reminder" && row.key==="planner:evt_planner_1:0:547")
                    && reminderRows.some(row=>row.kind==="agenda" && row.title==="Weekly standup" && row.key==="planner:evt_planner_weekly:0:549" && row.recurring===true)
                    && !reminderRows.some(row=>row.title==="Obsolete Obsidian schedule" || String(row.key).startsWith("journal:")),
                    "Weekly Planner/Todo reminder collection or Obsidian schedule retirement failed")
                WullMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
                WullMind.hostVisible=true;WullMind.hostIdle=true
                check(!WullMind.busy && WullMind.history.length===0,"opening Settings started inference")
                mouseMove(actor,actor.width/2,actor.height/2);wait(100)
                check(actions.visible && !WullMind.conversationOpen,"hover opened chat instead of cloud actions")
                mouseClick(root.named(actions,"wullObsidianAction"));wait(70)
                check(WullMind.contextOpen && !WullMind.conversationOpen,"Obsidian cloud opened chat input")
                WullMind.dismiss();mouseMove(actor,actor.width/2,actor.height/2);wait(70)
                mouseClick(root.named(actions,"wullAiAction"));wait(70)
                check(WullMind.conversationOpen && !WullMind.contextOpen,"AI cloud did not open explicit chat")
                tryCompare(WullMind,"busy",false,4000);WullMind.dismiss()
                WullMind.askCheckIn("mood");wait(80)
                check(cloud.visible && !cloud.editing && !WullMind.conversationOpen,"automatic talk stole conversation focus")
                const field=root.named(cloud,"wullChatInput"),background=root.named(cloud,"wullCloudBackground")
                mouseMove(root.contentItem,20,720);wait(80)
                check(cloud.controlsVisible && !field.visible && !field.activeFocus,"automatic check-in exposed input")
                check(background.border.width>0 && background.border.color.a>0 && field.placeholderText==="","speech lacks its border or retained an input hint")
                check(!root.named(settings,"wullReferenceVault"),"removed reference-vault control remains")
                const collapsedHeight=cloud.height
                mouseMove(cloud,cloud.width/2,14);wait(100)
                check(cloud.controlsVisible && !field.visible && !field.activeFocus && !WullMind.conversationOpen,"check-in hover exposed prompt input")
                check(root.named(cloud,"wullmood-good").visible && !root.named(cloud,"wullenergy-high").visible,"check-in showed two rows")
                check(cloud.height===collapsedHeight,"check-in changed size while answering")
                check(WullMind.testConnection(),"probe rejected")
                tryCompare(WullMind,"busy",false,6000)
                check(WullMind.connectionStatus==="ready" && WullMind.selectableModels.some(entry=>entry.name==="tiny:local"),"shared AI model not ready")
                mouseMove(root.contentItem,20,720);WullMind.openChat();wait(100)
                check(cloud.editing,"explicit chat did not open editor")
                tryCompare(WullMind,"busy",false,3000)
                const cloudGap=()=>Math.min(
                    Math.abs(cloud.actorVisualTop-(cloud.y+cloud.height)),
                    Math.abs(cloud.y-cloud.actorVisualBottom))
                check(cloudGap()<=cloud.anchorGap+.6 && cloud.anchorGap<=10,
                    "talk cloud is too far from Wull")
                const originalActorScale=actor.scale
                actor.scale=1.35;wait(30)
                check(Math.abs(cloud.actorVisualHeight-actor.height*1.35)<.6
                    && cloudGap()<=cloud.anchorGap+.6,
                    "talk cloud did not follow Wull's scaled bounds")
                actor.scale=originalActorScale;wait(20)
                const profileButton=root.named(cloud,"wullModelEffort")
                const effortSlider=root.named(cloud,"wullThinkingEffort")
                const modelPicker=root.named(cloud,"wullModelPicker")
                const composer=root.named(cloud,"wullChatComposer")
                const sendButton=root.named(cloud,"wullChatSend")
                check(!!composer && composer.visible && !!profileButton && profileButton.visible
                    && !!sendButton && sendButton.visible,
                    "chat composer/model effort selector missing")
                // Opening the composer schedules a Qt Layout polish. A visible
                // control can still belong to a zero-width parent in that turn.
                // Wait for the parent geometry, independently of the size gate.
                tryVerify(()=>Math.abs(composer.width-(cloud.width-24))<.6
                    && profileButton.implicitWidth>0,1000,"composer layout did not settle")
                const effortGeometry=JSON.stringify({width:profileButton.width,
                    implicitWidth:profileButton.implicitWidth,composerWidth:composer.width,
                    cloudWidth:cloud.width,label:WullMind.thinkingEffortShortLabel,
                    fontScale:Appearance.fontSizeScale})
                check(profileButton.width<130 && profileButton.width<composer.width/2,
                    "collapsed effort control did not size to its label "+effortGeometry)
                console.log("WULL_COMPOSER_GEOMETRY="+effortGeometry)
                check(Math.abs((profileButton.y+profileButton.height/2)-(sendButton.y+sendButton.height/2))<2,
                    "effort control and Send are not on the same composer row")
                mouseClick(profileButton);wait(50)
                check(cloud.profileStage===1 && !!effortSlider && effortSlider.visible
                    && !!modelPicker && !modelPicker.visible && !effortSlider.enabled
                    && WullMind.effectiveThinkingEffort==="off",
                    "first selector click did not show effort-only stage")
                mouseClick(profileButton);wait(50)
                check(cloud.profileStage===2 && modelPicker.visible && !effortSlider.visible,
                    "second selector click did not switch to model picker")
                const activeModelButton=root.named(cloud,"wullModelOption-tiny:local")
                const activeModelLabel=root.named(cloud,"wullModelLabel-tiny:local")
                check(!!activeModelButton && activeModelButton.visible && activeModelButton.toggled
                    && !!activeModelLabel && activeModelLabel.visible && activeModelLabel.text.length>0
                    && activeModelLabel.color===Appearance.colors.colOnPrimaryContainer,
                    "active model text lost contrast on selected background")
                mouseClick(profileButton);wait(30)
                check(cloud.profileStage===0 && field.activeFocus,
                    "third selector click did not close picker and return focus to composer")
                field.text="Hello Wull!"
                keyClick(Qt.Key_Return);wait(20)
                check(field.text==="","Enter did not submit the input")
                check(!!root.named(cloud,"wullChatSend") && !!root.named(cloud,"wullChatHistory")
                    && !root.named(cloud,"wullMessageSource") && !root.named(cloud,"wullCloudDismiss"),
                    "quick chat did not retain exactly the send control")
                tryCompare(WullMind,"busy",false,6000)
                check(WullMind.source==="ai" && WullMind.text.indexOf("Splish!")===0 && WullMind.history.length===2
                    && WullMind.history[0].role==="user" && WullMind.history[1].role==="assistant",
                    "local reply/history not presented")
                WullMind.closeChat();WullMind.history=[];WullMind.historyLoaded=false;WullMind.openChat()
                tryCompare(WullMind,"busy",false,3000)
                check(WullMind.history.length===2 && WullMind.history[0].role==="user"
                    && WullMind.history[1].role==="assistant","persisted quick-chat history did not reload")
                WullMind.closeChat();WullMind.conversationIdleTimeout=160;WullMind.openChat();wait(260)
                check(!WullMind.conversationOpen,"idle quick chat kept proactive reminders blocked")
                WullMind.conversationIdleTimeout=120000
                WullMind.clearConversation();check(WullMind.history.length===0,"clear retained history")
                tryCompare(WullMind,"historyClearPending",false,3000);tryCompare(WullMind,"busy",false,3000)
                check(WullMind.sendMessage("slow reply"),"cancellation request rejected")
                wait(45);const stale=WullMind.epoch;WullMind.cancel()
                WullMind.completed(JSON.stringify({ok:true,result:{text:"STALE",expression:"happy"}}),0,stale)
                check(WullMind.text!=="STALE" && WullMind.history.length===0,"stale reply applied after cancellation")
                tryVerify(()=>!WullMind.draining,3000)
                Ai.models["tiny:local"].endpoint="http://127.0.0.1:0";wait(50)
                check(!WullMind.sendMessage("Invalid connection") && !WullMind.busy && WullMind.errorMessage.length>0,
                    "invalid local endpoint reached inference")
                Ai.models["tiny:local"].endpoint=Quickshell.env("WULL_TEST_ENDPOINT")+"/failure";wait(50)
                check(WullMind.sendMessage("Provider failure"),"failure request not dispatched")
                tryCompare(WullMind,"busy",false,4000)
                check(WullMind.connectionStatus==="error" && WullMind.errorMessage.includes("429"),"provider failure looked ready")
                Ai.models["tiny:local"].local=false;Config.setNestedValue("policies.ai",2);wait(40)
                WullMind.clearConversation();WullMind.openChat()
                check(!WullMind.sendMessage("Hi!") && WullMind.errorMessage.length>0,"unavailable AI pretended inference")
                Ai.addModel("tiny:local",{name:"Tiny fixture",model:"tiny:local",local:true,
                    local_verified:true,local_fingerprint:Quickshell.env("WULL_TEST_LOCAL_FINGERPRINT"),provider_id:"lmstudio",
                    requires_key:false,api_format:"openai",endpoint:Quickshell.env("WULL_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"unsupported"}})
                Ai.modelList=Object.keys(Ai.models);Ai.currentModelId="tiny:local"
                WullMind.dismiss()
                const testVault=Quickshell.env("WULL_TEST_VAULT")
                Config.setNestedValues({"abyss.companionMind.obsidianEnabled":true,"todo.obsidian.vaultPath":testVault})
                tryCompare(WullMind,"obsidianEnabled",true,3000)
                tryVerify(()=>WullMind.payload("check_in").vault===testVault,3000)
                check(WullMind.obsidianEnabled && WullMind.payload("check_in").vault===testVault,
                    "journal bridge did not adopt configured vault "+JSON.stringify({
                        enabled:WullMind.obsidianEnabled,vault:WullMind.payload("check_in").vault,
                        configuredVault:String(Config.options?.todo?.obsidian?.vaultPath ?? "")}))
                const journalContextKey=WullMind.contextKey
                WullMind.askCheckIn("mood")
                mouseMove(cloud,cloud.width/2,14);wait(80)
                mouseClick(root.named(cloud,"wullmood-good"));wait(30)
                tryCompare(WullMind,"busy",false,6000)
                check(WullMind.userMood==="good" && WullMind.userEnergy==="",
                    "mood save/session mismatch "+JSON.stringify({mood:WullMind.userMood,energy:WullMind.userEnergy,
                        stage:WullMind.checkInStage,busy:WullMind.busy,pending:WullMind.pending?.action ?? ""}))
                check(WullMind.contextKey===journalContextKey,
                    "journal context identity changed after mood save "+JSON.stringify({
                        before:journalContextKey,after:WullMind.contextKey}))
                check(WullMind.checkInStage==="energy" && !root.named(cloud,"wullmood-good").visible
                    && root.named(cloud,"wullenergy-high").visible && !field.visible,"next question did not show energy alone")
                mouseMove(cloud,cloud.width/2,14);wait(50)
                mouseClick(root.named(cloud,"wullenergy-high"));wait(30)
                tryCompare(WullMind,"busy",false,6000)
                check(WullMind.userMood==="good" && WullMind.userEnergy==="high","Obsidian-style choice buttons did not set the session")
                check(WullMind.contextKey===journalContextKey,
                    "journal context identity changed after energy save "+JSON.stringify({
                        before:journalContextKey,after:WullMind.contextKey}))
                tryVerify(()=>String(WullMind.journal.journalPath ?? "").startsWith(testVault),3000)
                check(String(WullMind.journal.journalPath ?? "").startsWith(testVault),
                    "choices were not persisted through the helper "+JSON.stringify({
                        journalPath:String(WullMind.journal.journalPath ?? ""),
                        enabled:WullMind.obsidianEnabled,
                        vault:WullMind.payload("check_in").vault,
                        contextKey:WullMind.contextKey,
                        expectedContextKey:journalContextKey,
                        error:WullMind.errorMessage}))
                check(WullMind.checkInStage==="" && !root.named(cloud,"wullenergy-high").visible,"completed check-in retained choices")
                WullMind.openContext();wait(80)
                check(WullMind.contextOpen && WullMind.checkInStage==="" && !field.visible,"repeat context asked for the same daily choices")
                WullMind.clearConversation();tryCompare(WullMind,"historyClearPending",false,4000)
                check(WullMind.userMood==="good" && WullMind.userEnergy==="high"
                    && Persistent.states.wullCheckIn.date===WullMind.today(),"clearing chat erased the daily check-in")
                WullMind.askCheckIn("mood");wait(30)
                check(WullMind.checkInStage==="","completed daily check-in was asked again")
                check(!WullMind.setCheckInChoice("energy","anything"),"invalid choice accepted")
                field.text="saved draft"
                WullMind.openChat();wait(80)
                mouseMove(root.contentItem,20,720);wait(80)
                check(cloud.editing && field.visible && WullMind.conversationOpen,"explicit chat closed when the pointer left")
                check(field.text==="saved draft","hover leave lost the draft")
                keyClick(Qt.Key_Escape);wait(40)
                check(!cloud.visible && !WullMind.conversationOpen,"Escape did not close chat")
                check(root.outsideClicks===0,"speech controls counted as nearby disturbance")
                mouseClick(root.contentItem,20,720);wait(30)
                check(root.outsideClicks===1,"speech guard blocked clicks outside the cloud")
                WullMind.dismiss();check(!cloud.visible,"dismiss retained speech input")
                check(!WullMind.memoryEnabled,"memory defaulted on")
                Config.setNestedValue("abyss.companionMind.memoryEnabled",true)
                tryVerify(()=>WullMind.memoriesLoaded && !WullMind.busy,4000)
                check(WullMind.memories.length===0,"private memory store did not start empty")
                check(WullMind.saveMemory("Tea is nice","shared",7),"explicit memory save rejected")
                tryVerify(()=>WullMind.memoriesLoaded && WullMind.memories.length===1 && !WullMind.busy,4000)
                const memoryId=WullMind.memories[0].id
                check(WullMind.memoryContext(Date.now()).includes("Tea is nice"),"shared memory not attached for local model")
                check(WullMind.saveMemory("Octo prefers tea","octo",30,memoryId),"explicit memory edit rejected")
                tryVerify(()=>WullMind.memoriesLoaded && WullMind.memories[0]?.scope==="octo" && !WullMind.busy,4000)
                WullMind.character="aqua"
                check(WullMind.memoryContext(Date.now()).length===0,"Octo memory leaked to Aqua")
                WullMind.character="octo"
                check(WullMind.memoryContext(Date.now())[0]==="Octo prefers tea","cast memory missing")
                Config.setNestedValue("abyss.companionMind.memoryEnabled",false)
                check(WullMind.memoryContext(Date.now()).length===0,"memory revocation retained model context")
                check(WullMind.forgetMemory(memoryId),"forget while disabled rejected")
                tryVerify(()=>WullMind.memoriesLoaded && WullMind.memories.length===0 && !WullMind.busy,4000)
                check(WullMind.forgetAllMemories(),"empty forget-all rejected")
                tryCompare(WullMind,"busy",false,4000)
                check(root.named(settings,"wullEnableMemory") && root.named(settings,"wullMemoryDraft")
                    && root.named(settings,"wullReviewMemory") && root.named(settings,"wullForgetAllMemory"),"memory management controls missing")
                check(!WullMind.dialoguePreferences.enabled,"dialogue personalization defaulted on")
                Config.setNestedValues({"abyss.companionMind.bondEnabled":true,
                    "abyss.companionMind.dialogueTone":"concise","abyss.companionMind.familiarity":"familiar"})
                check(WullMind.dialoguePreferences.enabled && WullMind.sendMessage("Dialogue fixture"),"consented local dialogue rejected")
                tryCompare(WullMind,"busy",false,6000)
                check(WullMind.history[WullMind.history.length-1]?.role==="assistant","personalized dialogue failed")
                check(WullMind.sendMessage("slow dialogue fixture"),"dialogue revocation setup failed")
                WullMind.resetDialogue()
                const resetKey=WullMind.dialogueKey
                tryCompare(WullMind.aiSession,"busy",false,4000)
                check(!WullMind.dialoguePreferences.enabled && !WullMind.history.some(row=>row.content==="slow dialogue fixture")
                    && WullMind.userMood==="good" && WullMind.userEnergy==="high" && WullMind.memories.length===0,
                    "dialogue reset retained a request or changed independent check-in/memory state")
                check(root.named(settings,"wullDialogueConsent") && root.named(settings,"wullResetDialogue"),"dialogue controls missing")
                check(root.named(settings,"wullPlannerReminders") && root.named(settings,"wullCalendarReminders")
                    && !root.named(settings,"wullJournalReminders"),
                    "Weekly Planner reminder toggle missing or retired journal reminder toggle remained")
                const permGrid=root.named(settings,"wullAiPermissionsGrid")
                const modelGrid=root.named(settings,"wullAiModelGrid")
                const quotaGrid=root.named(settings,"wullProactiveQuotaGrid")
                const quietGrid=root.named(settings,"wullQuietHoursGrid")
                const sourceGrid=root.named(settings,"wullReminderSourcesGrid")
                const dialogueGrid=root.named(settings,"wullDialogueGrid")
                const plannerToggle=root.named(settings,"wullPlannerReminders")
                settings.visible=true;settings.width=800;wait(40)
                check(!!permGrid && permGrid.columns>=2 && !!modelGrid && modelGrid.columns===2
                    && !!quotaGrid && quotaGrid.columns>=2 && !!quietGrid && quietGrid.columns>=2
                    && !!sourceGrid && sourceGrid.columns>=2 && !!dialogueGrid && dialogueGrid.columns>=2
                    && plannerToggle.buttonIcon==="event_upcoming" && plannerToggle.activeFocusOnTab,
                    "compact multi-column AI settings layout did not activate at normal width")
                settings.width=340;wait(40)
                check(permGrid.columns===1 && modelGrid.columns===1 && quotaGrid.columns===1
                    && quietGrid.columns===1 && sourceGrid.columns===1 && dialogueGrid.columns===1,
                    "compact AI settings layout did not collapse cleanly on narrow width")
                settings.width=800;settings.visible=false
                console.log("WULL_MIND=PASS actualProcess sharedModelReadiness EnglishReply hoverCloudActions dailyChoices readOnlyContext borderedCloud noCheckInInput separateQuestions journalWrites stableJournalContext explicitChatFocus modelEffortSelector compactEffortRow activeModelContrast stagedEffortModelPicker enterSend sendOnlyControl persistentHistory reminderSources weeklyPlannerEvents noJournalReminders compactAiSettings proactiveCadences scaledTalkCloudAnchor idleChatRelease retainedDraft escapeClose boundedHistory cancel staleReply invalidEndpoint unavailableModel settingsAI noReferenceVault explicitMemory save edit castScope revocation forget managementControls")
            } catch(e) {console.error("WULL_MIND=FAIL "+e+" "+e.stack)}
            shutdown.start()
        }
    }
    Timer {interval:200;running:Config.ready;onTriggered:input.runChecks()}
    Timer {id:shutdown;interval:150;onTriggered:Qt.quit()}
}
