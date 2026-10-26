import QtQuick
import QtTest
import Quickshell
import qs.services
import "../../../optional/alis-companion/services"
import qs.modules.common
import qs.modules.settings
import "../../../optional/alis-companion/modules/abyss/companion"
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
    AquaTalkCloud {id:cloud;actor:actor;outputWidth:root.width;outputHeight:root.height;allowed:true}
    AquaCloudActions {id:actions;actor:actor;outputWidth:root.width;outputHeight:root.height;allowed:true}
    CompanionConfig {id:settings;visible:false;width:800;height:700;activeSection:"overview"}
    TestCase {
        id:input;when:false;optional:true
        function check(value,message):void {if(!value)throw new Error(message)}
        function runChecks():void {
            try {
                tryCompare(Config,"ready",true,4000)
                Ai._initialized=true
                Ai.addModel("tiny:local",{name:"Tiny fixture",model:"tiny:local",local:true,
                    local_verified:true,local_fingerprint:Quickshell.env("AQUA_TEST_LOCAL_FINGERPRINT"),provider_id:"lmstudio",
                    requires_key:false,api_format:"openai",endpoint:Quickshell.env("AQUA_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"unsupported"}})
                Ai.modelList=Object.keys(Ai.models);Ai.currentModelId="tiny:local"
                settings.activeSection="ai"
                Config.setNestedValues({"abyss.companionMind.proactive":"manual",
                    "abyss.companionMind.model":"tiny:local","abyss.companionMind.obsidianEnabled":false})
                wait(80)
                const cadences=AquaMind.proactiveProfiles
                check(AquaMind.proactive==="manual" && !AquaMind.proactiveIdleEnabled
                    && cadences.rare.checkInMs>cadences.occasional.checkInMs
                    && cadences.occasional.checkInMs>cadences.regular.checkInMs
                    && cadences.regular.checkInMs>cadences.often.checkInMs
                    && cadences.rare.playfulMs>cadences.occasional.playfulMs
                    && cadences.occasional.playfulMs>cadences.regular.playfulMs
                    && cadences.regular.playfulMs>cadences.often.playfulMs,
                    "proactive cadence profiles are not ordered or manual-safe")
                const syntheticNow=new Date(2026,9,5,9,0,0)
                check(AquaMind.reminderRows(syntheticNow,[],[]).length===0,"reminder sources default to private")
                Config.setNestedValues({"abyss.companionMind.todoRemindersEnabled":true,
                    "abyss.companionMind.calendarRemindersEnabled":true,
                    "abyss.companionMind.journalRemindersEnabled":true})
                AquaMind.journal=({date:"2026-10-05",schedule:[{start:545,title:"Obsolete Obsidian schedule",kind:"schedule"}],mood:"",energy:"",journalPath:""})
                const reminderRows=AquaMind.reminderRows(syntheticNow,
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
                AquaMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
                AquaMind.hostVisible=true;AquaMind.hostIdle=true
                check(!AquaMind.busy && AquaMind.history.length===0,"opening Settings started inference")
                mouseMove(actor,actor.width/2,actor.height/2);wait(100)
                check(actions.visible && !AquaMind.conversationOpen,"hover opened chat instead of cloud actions")
                mouseClick(root.named(actions,"aquaObsidianAction"));wait(70)
                check(AquaMind.contextOpen && !AquaMind.conversationOpen,"Obsidian cloud opened chat input")
                AquaMind.dismiss();mouseMove(actor,actor.width/2,actor.height/2);wait(70)
                mouseClick(root.named(actions,"aquaAiAction"));wait(70)
                check(AquaMind.conversationOpen && !AquaMind.contextOpen,"AI cloud did not open explicit chat")
                tryCompare(AquaMind,"busy",false,4000);AquaMind.dismiss()
                AquaMind.askCheckIn("mood");wait(80)
                check(cloud.visible && !cloud.editing && !AquaMind.conversationOpen,"automatic talk stole conversation focus")
                const field=root.named(cloud,"aquaChatInput"),background=root.named(cloud,"aquaCloudBackground")
                mouseMove(root.contentItem,20,720);wait(80)
                check(cloud.controlsVisible && !field.visible && !field.activeFocus,"automatic check-in exposed input")
                check(background.border.width>0 && background.border.color.a>0 && field.placeholderText==="","speech lacks its border or retained an input hint")
                check(!root.named(settings,"aquaReferenceVault"),"removed reference-vault control remains")
                const collapsedHeight=cloud.height
                mouseMove(cloud,cloud.width/2,14);wait(100)
                check(cloud.controlsVisible && !field.visible && !field.activeFocus && !AquaMind.conversationOpen,"check-in hover exposed prompt input")
                check(root.named(cloud,"aquamood-good").visible && !root.named(cloud,"aquaenergy-high").visible,"check-in showed two rows")
                check(cloud.height===collapsedHeight,"check-in changed size while answering")
                check(AquaMind.testConnection(),"probe rejected")
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.connectionStatus==="ready" && AquaMind.selectableModels.some(entry=>entry.name==="tiny:local"),"shared AI model not ready")
                mouseMove(root.contentItem,20,720);AquaMind.openChat();wait(100)
                check(cloud.editing,"explicit chat did not open editor")
                tryCompare(AquaMind,"busy",false,3000)
                const cloudGap=()=>Math.min(
                    Math.abs(cloud.actorVisualTop-(cloud.y+cloud.height)),
                    Math.abs(cloud.y-cloud.actorVisualBottom))
                check(cloudGap()<=cloud.anchorGap+.6 && cloud.anchorGap<=10,
                    "talk cloud is too far from Aqua")
                const originalActorScale=actor.scale
                actor.scale=1.35;wait(30)
                check(Math.abs(cloud.actorVisualHeight-actor.height*1.35)<.6
                    && cloudGap()<=cloud.anchorGap+.6,
                    "talk cloud did not follow Aqua's scaled bounds")
                actor.scale=originalActorScale;wait(20)
                const profileButton=root.named(cloud,"aquaModelEffort")
                const effortSlider=root.named(cloud,"aquaThinkingEffort")
                const modelPicker=root.named(cloud,"aquaModelPicker")
                const composer=root.named(cloud,"aquaChatComposer")
                const sendButton=root.named(cloud,"aquaChatSend")
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
                    cloudWidth:cloud.width,label:AquaMind.thinkingEffortShortLabel,
                    fontScale:Appearance.fontSizeScale})
                check(profileButton.width<130 && profileButton.width<composer.width/2,
                    "collapsed effort control did not size to its label "+effortGeometry)
                console.log("AQUA_COMPOSER_GEOMETRY="+effortGeometry)
                check(Math.abs((profileButton.y+profileButton.height/2)-(sendButton.y+sendButton.height/2))<2,
                    "effort control and Send are not on the same composer row")
                mouseClick(profileButton);wait(50)
                check(cloud.profileStage===1 && !!effortSlider && effortSlider.visible
                    && !!modelPicker && !modelPicker.visible && !effortSlider.enabled
                    && AquaMind.effectiveThinkingEffort==="off",
                    "first selector click did not show effort-only stage")
                mouseClick(profileButton);wait(50)
                check(cloud.profileStage===2 && modelPicker.visible && !effortSlider.visible,
                    "second selector click did not switch to model picker")
                const activeModelButton=root.named(cloud,"aquaModelOption-tiny:local")
                const activeModelLabel=root.named(cloud,"aquaModelLabel-tiny:local")
                check(!!activeModelButton && activeModelButton.visible && activeModelButton.toggled
                    && !!activeModelLabel && activeModelLabel.visible && activeModelLabel.text.length>0
                    && activeModelLabel.color===Appearance.colors.colOnPrimaryContainer,
                    "active model text lost contrast on selected background")
                mouseClick(profileButton);wait(30)
                check(cloud.profileStage===0 && field.activeFocus,
                    "third selector click did not close picker and return focus to composer")
                field.text="Hello Aqua!"
                keyClick(Qt.Key_Return);wait(20)
                check(field.text==="","Enter did not submit the input")
                check(!!root.named(cloud,"aquaChatSend") && !!root.named(cloud,"aquaChatHistory")
                    && !root.named(cloud,"aquaMessageSource") && !root.named(cloud,"aquaCloudDismiss"),
                    "quick chat did not retain exactly the send control")
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.source==="ai" && AquaMind.text.indexOf("Splish!")===0 && AquaMind.history.length===2
                    && AquaMind.history[0].role==="user" && AquaMind.history[1].role==="assistant",
                    "local reply/history not presented")
                AquaMind.closeChat();AquaMind.history=[];AquaMind.historyLoaded=false;AquaMind.openChat()
                tryCompare(AquaMind,"busy",false,3000)
                check(AquaMind.history.length===2 && AquaMind.history[0].role==="user"
                    && AquaMind.history[1].role==="assistant","persisted quick-chat history did not reload")
                AquaMind.closeChat();AquaMind.conversationIdleTimeout=160;AquaMind.openChat();wait(260)
                check(!AquaMind.conversationOpen,"idle quick chat kept proactive reminders blocked")
                AquaMind.conversationIdleTimeout=120000
                AquaMind.clearConversation();check(AquaMind.history.length===0,"clear retained history")
                tryCompare(AquaMind,"historyClearPending",false,3000);tryCompare(AquaMind,"busy",false,3000)
                check(AquaMind.sendMessage("slow reply"),"cancellation request rejected")
                wait(45);const stale=AquaMind.epoch;AquaMind.cancel()
                AquaMind.completed(JSON.stringify({ok:true,result:{text:"STALE",expression:"happy"}}),0,stale)
                check(AquaMind.text!=="STALE" && AquaMind.history.length===0,"stale reply applied after cancellation")
                tryVerify(()=>!AquaMind.draining,3000)
                Ai.models["tiny:local"].endpoint="http://127.0.0.1:0";wait(50)
                check(!AquaMind.sendMessage("Invalid connection") && !AquaMind.busy && AquaMind.errorMessage.length>0,
                    "invalid local endpoint reached inference")
                Ai.models["tiny:local"].endpoint=Quickshell.env("AQUA_TEST_ENDPOINT")+"/failure";wait(50)
                check(AquaMind.sendMessage("Provider failure"),"failure request not dispatched")
                tryCompare(AquaMind,"busy",false,4000)
                check(AquaMind.connectionStatus==="error" && AquaMind.errorMessage.includes("429"),"provider failure looked ready")
                Ai.models["tiny:local"].local=false;Config.setNestedValue("policies.ai",2);wait(40)
                AquaMind.clearConversation();AquaMind.openChat()
                check(!AquaMind.sendMessage("Hi!") && AquaMind.errorMessage.length>0,"unavailable AI pretended inference")
                Ai.addModel("tiny:local",{name:"Tiny fixture",model:"tiny:local",local:true,
                    local_verified:true,local_fingerprint:Quickshell.env("AQUA_TEST_LOCAL_FINGERPRINT"),provider_id:"lmstudio",
                    requires_key:false,api_format:"openai",endpoint:Quickshell.env("AQUA_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"unsupported"}})
                Ai.modelList=Object.keys(Ai.models);Ai.currentModelId="tiny:local"
                AquaMind.dismiss()
                const testVault=Quickshell.env("AQUA_TEST_VAULT")
                Config.setNestedValues({"abyss.companionMind.obsidianEnabled":true,"todo.obsidian.vaultPath":testVault})
                tryCompare(AquaMind,"obsidianEnabled",true,3000)
                tryVerify(()=>AquaMind.payload("check_in").vault===testVault,3000)
                check(AquaMind.obsidianEnabled && AquaMind.payload("check_in").vault===testVault,
                    "journal bridge did not adopt configured vault "+JSON.stringify({
                        enabled:AquaMind.obsidianEnabled,vault:AquaMind.payload("check_in").vault,
                        configuredVault:String(Config.options?.todo?.obsidian?.vaultPath ?? "")}))
                const journalContextKey=AquaMind.contextKey
                AquaMind.askCheckIn("mood")
                mouseMove(cloud,cloud.width/2,14);wait(80)
                mouseClick(root.named(cloud,"aquamood-good"));wait(30)
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.userMood==="good" && AquaMind.userEnergy==="",
                    "mood save/session mismatch "+JSON.stringify({mood:AquaMind.userMood,energy:AquaMind.userEnergy,
                        stage:AquaMind.checkInStage,busy:AquaMind.busy,pending:AquaMind.pending?.action ?? ""}))
                check(AquaMind.contextKey===journalContextKey,
                    "journal context identity changed after mood save "+JSON.stringify({
                        before:journalContextKey,after:AquaMind.contextKey}))
                check(AquaMind.checkInStage==="energy" && !root.named(cloud,"aquamood-good").visible
                    && root.named(cloud,"aquaenergy-high").visible && !field.visible,"next question did not show energy alone")
                mouseMove(cloud,cloud.width/2,14);wait(50)
                mouseClick(root.named(cloud,"aquaenergy-high"));wait(30)
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.userMood==="good" && AquaMind.userEnergy==="high","Obsidian-style choice buttons did not set the session")
                check(AquaMind.contextKey===journalContextKey,
                    "journal context identity changed after energy save "+JSON.stringify({
                        before:journalContextKey,after:AquaMind.contextKey}))
                tryVerify(()=>String(AquaMind.journal.journalPath ?? "").startsWith(testVault),3000)
                check(String(AquaMind.journal.journalPath ?? "").startsWith(testVault),
                    "choices were not persisted through the helper "+JSON.stringify({
                        journalPath:String(AquaMind.journal.journalPath ?? ""),
                        enabled:AquaMind.obsidianEnabled,
                        vault:AquaMind.payload("check_in").vault,
                        contextKey:AquaMind.contextKey,
                        expectedContextKey:journalContextKey,
                        error:AquaMind.errorMessage}))
                check(AquaMind.checkInStage==="" && !root.named(cloud,"aquaenergy-high").visible,"completed check-in retained choices")
                AquaMind.openContext();wait(80)
                check(AquaMind.contextOpen && AquaMind.checkInStage==="" && !field.visible,"repeat context asked for the same daily choices")
                AquaMind.clearConversation();tryCompare(AquaMind,"historyClearPending",false,4000)
                check(AquaMind.userMood==="good" && AquaMind.userEnergy==="high"
                    && Persistent.states.aquaCheckIn.date===AquaMind.today(),"clearing chat erased the daily check-in")
                AquaMind.askCheckIn("mood");wait(30)
                check(AquaMind.checkInStage==="","completed daily check-in was asked again")
                check(!AquaMind.setCheckInChoice("energy","anything"),"invalid choice accepted")
                field.text="saved draft"
                AquaMind.openChat();wait(80)
                mouseMove(root.contentItem,20,720);wait(80)
                check(cloud.editing && field.visible && AquaMind.conversationOpen,"explicit chat closed when the pointer left")
                check(field.text==="saved draft","hover leave lost the draft")
                keyClick(Qt.Key_Escape);wait(40)
                check(!cloud.visible && !AquaMind.conversationOpen,"Escape did not close chat")
                check(root.outsideClicks===0,"speech controls counted as nearby disturbance")
                mouseClick(root.contentItem,20,720);wait(30)
                check(root.outsideClicks===1,"speech guard blocked clicks outside the cloud")
                AquaMind.dismiss();check(!cloud.visible,"dismiss retained speech input")
                check(!AquaMind.memoryEnabled,"memory defaulted on")
                Config.setNestedValue("abyss.companionMind.memoryEnabled",true)
                tryVerify(()=>AquaMind.memoriesLoaded && !AquaMind.busy,4000)
                check(AquaMind.memories.length===0,"private memory store did not start empty")
                check(AquaMind.saveMemory("Tea is nice","shared",7),"explicit memory save rejected")
                tryVerify(()=>AquaMind.memoriesLoaded && AquaMind.memories.length===1 && !AquaMind.busy,4000)
                const memoryId=AquaMind.memories[0].id
                check(AquaMind.memoryContext(Date.now()).includes("Tea is nice"),"shared memory not attached for local model")
                check(AquaMind.saveMemory("Octo prefers tea","octo",30,memoryId),"explicit memory edit rejected")
                tryVerify(()=>AquaMind.memoriesLoaded && AquaMind.memories[0]?.scope==="octo" && !AquaMind.busy,4000)
                AquaMind.character="aqua"
                check(AquaMind.memoryContext(Date.now()).length===0,"Octo memory leaked to Aqua")
                AquaMind.character="octo"
                check(AquaMind.memoryContext(Date.now())[0]==="Octo prefers tea","cast memory missing")
                Config.setNestedValue("abyss.companionMind.memoryEnabled",false)
                check(AquaMind.memoryContext(Date.now()).length===0,"memory revocation retained model context")
                check(AquaMind.forgetMemory(memoryId),"forget while disabled rejected")
                tryVerify(()=>AquaMind.memoriesLoaded && AquaMind.memories.length===0 && !AquaMind.busy,4000)
                check(AquaMind.forgetAllMemories(),"empty forget-all rejected")
                tryCompare(AquaMind,"busy",false,4000)
                check(root.named(settings,"aquaEnableMemory") && root.named(settings,"aquaMemoryDraft")
                    && root.named(settings,"aquaReviewMemory") && root.named(settings,"aquaForgetAllMemory"),"memory management controls missing")
                check(!AquaMind.dialoguePreferences.enabled,"dialogue personalization defaulted on")
                Config.setNestedValues({"abyss.companionMind.bondEnabled":true,
                    "abyss.companionMind.dialogueTone":"concise","abyss.companionMind.familiarity":"familiar"})
                check(AquaMind.dialoguePreferences.enabled && AquaMind.sendMessage("Dialogue fixture"),"consented local dialogue rejected")
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.history[AquaMind.history.length-1]?.role==="assistant","personalized dialogue failed")
                check(AquaMind.sendMessage("slow dialogue fixture"),"dialogue revocation setup failed")
                AquaMind.resetDialogue()
                const resetKey=AquaMind.dialogueKey
                tryCompare(AquaMind.aiSession,"busy",false,4000)
                check(!AquaMind.dialoguePreferences.enabled && !AquaMind.history.some(row=>row.content==="slow dialogue fixture")
                    && AquaMind.userMood==="good" && AquaMind.userEnergy==="high" && AquaMind.memories.length===0,
                    "dialogue reset retained a request or changed independent check-in/memory state")
                check(root.named(settings,"aquaDialogueConsent") && root.named(settings,"aquaResetDialogue"),"dialogue controls missing")
                check(root.named(settings,"aquaPlannerReminders") && root.named(settings,"aquaCalendarReminders")
                    && !root.named(settings,"aquaJournalReminders"),
                    "Weekly Planner reminder toggle missing or retired journal reminder toggle remained")
                const permGrid=root.named(settings,"aquaAiPermissionsGrid")
                const modelGrid=root.named(settings,"aquaAiModelGrid")
                const quotaGrid=root.named(settings,"aquaProactiveQuotaGrid")
                const quietGrid=root.named(settings,"aquaQuietHoursGrid")
                const sourceGrid=root.named(settings,"aquaReminderSourcesGrid")
                const dialogueGrid=root.named(settings,"aquaDialogueGrid")
                const plannerToggle=root.named(settings,"aquaPlannerReminders")
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
                console.log("AQUA_MIND=PASS actualProcess sharedModelReadiness EnglishReply hoverCloudActions dailyChoices readOnlyContext borderedCloud noCheckInInput separateQuestions journalWrites stableJournalContext explicitChatFocus modelEffortSelector compactEffortRow activeModelContrast stagedEffortModelPicker enterSend sendOnlyControl persistentHistory reminderSources weeklyPlannerEvents noJournalReminders compactAiSettings proactiveCadences scaledTalkCloudAnchor idleChatRelease retainedDraft escapeClose boundedHistory cancel staleReply invalidEndpoint unavailableModel settingsAI noReferenceVault explicitMemory save edit castScope revocation forget managementControls")
            } catch(e) {console.error("AQUA_MIND=FAIL "+e+" "+e.stack)}
            shutdown.start()
        }
    }
    Timer {interval:200;running:Config.ready;onTriggered:input.runChecks()}
    Timer {id:shutdown;interval:150;onTriggered:Qt.quit()}
}
