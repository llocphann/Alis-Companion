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
    AquaCompanionChatView {id:embeddedView;visible:false;width:420;height:640}
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
                const unifiedHeader=root.named(cloud,"aquaUnifiedHeader")
                const unifiedChatTab=root.named(cloud,"aquaUnifiedChatTab")
                const unifiedTodayTab=root.named(cloud,"aquaUnifiedTodayTab")
                const unifiedPomodoroBar=root.named(cloud,"aquaUnifiedPomodoroBar")
                const unifiedPomodoroToggle=root.named(cloud,"aquaUnifiedPomodoroToggle")
                const unifiedSettingsBtn=root.named(cloud,"aquaUnifiedSettingsButton")
                const unifiedCloseBtn=root.named(cloud,"aquaPopupClose")
                const chatTodayBar=root.named(cloud,"aquaChatTodayBar")
                const chatMoodBtn=root.named(cloud,"aquaChatMoodButton")
                const chatEnergyBtn=root.named(cloud,"aquaChatEnergyButton")
                const chatTodayPhrase=root.named(cloud,"aquaChatTodayPhrase")
                const chatCheckInChoices=root.named(cloud,"aquaChatCheckInChoices")
                check(!!unifiedHeader && unifiedHeader.visible && !!unifiedChatTab && unifiedChatTab.visible
                    && !unifiedTodayTab && !!unifiedPomodoroBar && unifiedPomodoroBar.visible
                    && !!unifiedPomodoroToggle && unifiedPomodoroToggle.visible
                    && !!chatTodayBar && chatTodayBar.visible && !!chatMoodBtn && chatMoodBtn.visible
                    && !!chatEnergyBtn && chatEnergyBtn.visible && !!chatTodayPhrase && chatTodayPhrase.visible
                    && !!chatCheckInChoices && chatCheckInChoices.visible
                    && !unifiedSettingsBtn && !unifiedCloseBtn,
                    "unified Mochi-style Abyss popup header, Pomodoro bar, or integrated Chat Today Energy/Mood card missing")
                mouseClick(chatEnergyBtn);wait(40)
                check(cloud.activeChatCheckInField==="energy" && !!root.named(cloud,"aquaChatChoice-energy-high")?.visible,
                    "clicking Energy button inside chatbox did not switch inline picker to Energy options")
                mouseClick(chatEnergyBtn);wait(40)
                check(cloud.activeChatCheckInField==="" && !chatCheckInChoices.visible,
                    "clicking Energy button again did not collapse inline picker")
                cloud.chatCheckInField=""
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
                const modelSelector=root.named(cloud,"aquaModelSelector")
                const profileButton=root.named(cloud,"aquaModelEffort")
                const effortSlider=root.named(cloud,"aquaThinkingEffort")
                const modelPicker=root.named(cloud,"aquaModelPicker")
                const composer=root.named(cloud,"aquaChatComposer")
                const sendButton=root.named(cloud,"aquaChatSend")
                check(!!composer && composer.visible && !!modelSelector && modelSelector.visible
                    && !!profileButton && !profileButton.visible && !!sendButton && sendButton.visible,
                    "chat composer model selector missing or effort button not hidden when reasoning unsupported")
                tryVerify(()=>Math.abs(composer.width-(cloud.width-24))<.6
                    && modelSelector.implicitWidth>0,1000,"composer layout did not settle")
                check(Math.abs((modelSelector.y+modelSelector.height/2)-(sendButton.y+sendButton.height/2))<2,
                    "model selector and Send are not on the same composer row")
                mouseClick(modelSelector);wait(50)
                check(cloud.profileStage===2 && !!modelPicker && modelPicker.visible && !!effortSlider && !effortSlider.visible,
                    "model selector click did not open model picker")
                const activeModelButton=root.named(cloud,"aquaModelOption-tiny:local")
                const activeModelLabel=root.named(cloud,"aquaModelLabel-tiny:local")
                check(!!activeModelButton && activeModelButton.visible && activeModelButton.toggled
                    && !!activeModelLabel && activeModelLabel.visible && activeModelLabel.text.length>0
                    && activeModelLabel.color===Appearance.colors.colOnPrimaryContainer,
                    "active model text lost contrast on selected background")
                mouseClick(modelSelector);wait(30)
                check(cloud.profileStage===0 && field.activeFocus,
                    "second model selector click did not close model picker and return focus to composer")
                Ai.models["tiny:local"].capabilities={chat:"supported",reasoning:"supported"};wait(40)
                check(AquaMind.thinkingSupported && profileButton.visible,
                    "effort selector button did not appear when reasoning supported")
                const effortGeometry=JSON.stringify({width:profileButton.width,
                    implicitWidth:profileButton.implicitWidth,composerWidth:composer.width,
                    cloudWidth:cloud.width,label:AquaMind.thinkingEffortShortLabel,
                    fontScale:Appearance.fontSizeScale})
                check(profileButton.width<130 && profileButton.width<composer.width/2,
                    "collapsed effort control did not size to its label "+effortGeometry)
                console.log("AQUA_COMPOSER_GEOMETRY="+effortGeometry)
                mouseClick(profileButton);wait(50)
                check(cloud.profileStage===1 && effortSlider.visible && !modelPicker.visible
                    && !!root.named(cloud,"aquaEffortOption-off")?.visible
                    && !!root.named(cloud,"aquaEffortOption-low")?.visible
                    && !!root.named(cloud,"aquaEffortOption-medium")?.visible
                    && !!root.named(cloud,"aquaEffortOption-high")?.visible
                    && !!root.named(cloud,"aquaEffortOption-xhigh")?.visible
                    && !!root.named(cloud,"aquaEffortOption-max")?.visible,
                    "effort button click did not show drop-up menu with Instant/Low/Medium/High/Extra High/Max")
                mouseClick(root.named(cloud,"aquaEffortOption-xhigh"));wait(40)
                check(AquaMind.thinkingEffort==="xhigh" && cloud.profileStage===0 && field.activeFocus,
                    "selecting Extra High effort option did not apply and close effort menu")
                AquaMind.setThinkingEffort("off")
                Ai.models["tiny:local"].capabilities={chat:"supported",reasoning:"unsupported"};wait(40)
                check(!AquaMind.thinkingSupported && !profileButton.visible,
                    "effort selector button did not hide again when reasoning became unsupported")
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
                AquaMind.memories=[{id:1,text:"Remember tea",scope:"shared",expires_ms:0}];AquaMind.memoriesLoaded=true
                check(AquaMind.sendMessage("/clear"),"/clear command rejected")
                check(AquaMind.history.length===0 && AquaMind.memories.length===1,"clear retained history or erased memories")
                AquaMind.memories=[];AquaMind.memoriesLoaded=false
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
                Ai.models["tiny:local"].local=false;Ai.modelList=Object.keys(Ai.models);wait(40)
                AquaMind.clearConversation();tryCompare(AquaMind,"historyClearPending",false,3000);tryCompare(AquaMind,"busy",false,3000);AquaMind.openChat()
                check(!AquaMind.sendMessage("Hi!") && AquaMind.errorMessage.length>0,"unavailable AI pretended inference")
                Ai.addModel("tiny:local",{name:"Tiny fixture",model:"tiny:local",local:true,
                    local_verified:true,local_fingerprint:Quickshell.env("AQUA_TEST_LOCAL_FINGERPRINT"),provider_id:"lmstudio",
                    requires_key:false,api_format:"openai",endpoint:Quickshell.env("AQUA_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"unsupported"}})
                Ai.modelList=Object.keys(Ai.models);Ai.currentModelId="tiny:local"
                AquaMind.dismiss()
                const testVault=Quickshell.env("AQUA_TEST_VAULT")
                Config.setNestedValues({"abyss.companionMind.obsidianEnabled":true,"todo.obsidian.vaultPath":testVault})
                wait(40)
                check(!AquaMind.obsidianEnabled && AquaMind.payload("check_in").vault===""
                    && !root.named(settings,"aquaConnectObsidian") && !root.named(cloud,"aquaChatOpenJournal"),
                    "retired Obsidian integration was still active in Companion")
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
                tryVerify(()=>root.named(cloud,"aquaenergy-high").enabled && !AquaMind.busy,2000)
                mouseClick(root.named(cloud,"aquaenergy-high"));wait(30)
                tryCompare(AquaMind,"busy",false,6000)
                check(AquaMind.userMood==="good" && AquaMind.userEnergy==="high","choice buttons did not set the session")
                check(AquaMind.contextKey===journalContextKey,
                    "journal context identity changed after energy save "+JSON.stringify({
                        before:journalContextKey,after:AquaMind.contextKey}))
                check(String(AquaMind.journal.journalPath ?? "")==="",
                    "retired Obsidian integration still wrote a vault journal path")
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
                AquaMind.openChat();wait(60)
                // Verify Today bar is compact once Mood & Energy check-in is complete, and expands on hover
                const chatMoodLabel=root.named(cloud,"aquaChatMoodLabel")
                const chatEnergyLabel=root.named(cloud,"aquaChatEnergyLabel")
                check(AquaMind.checkInComplete && cloud.activeChatCheckInField==="" && !cloud.todayBarHovered
                    && !chatTodayPhrase.visible && chatTodayBar.height<=34
                    && !!chatMoodLabel && chatMoodLabel.text==="good"
                    && !!chatEnergyLabel && chatEnergyLabel.text==="high",
                    "Today Energy/Mood bar or pill labels did not collapse into compact single-row state after check-in completed")
                const compactTodayHeight=chatTodayBar.height
                cloud.forceTodayBarHover=true;mouseMove(chatMoodBtn,chatMoodBtn.width/2,chatMoodBtn.height/2);wait(45)
                check(cloud.todayBarHovered && cloud.todayBarExpanded && chatTodayPhrase.visible
                    && chatTodayBar.expandProgress>0 && chatTodayBar.height>compactTodayHeight
                    && chatMoodLabel.text.indexOf("Mood:")===0 && chatEnergyLabel.text.indexOf("Energy:")===0,
                    "Hovering Today Mood pill did not animate Today bar expansion to show full labels and contextual phrase: "
                    + JSON.stringify({
                        hovered: cloud.todayBarHovered,
                        expanded: cloud.todayBarExpanded,
                        phraseVis: chatTodayPhrase.visible,
                        progress: chatTodayBar.expandProgress,
                        h: chatTodayBar.height,
                        compactH: compactTodayHeight,
                        targetH: chatTodayBar.targetHeight,
                        expandedTargetH: chatTodayBar.expandedTargetHeight,
                        moodTxt: chatMoodLabel.text,
                        energyTxt: chatEnergyLabel.text
                    }))
                tryVerify(()=>chatTodayBar.expandProgress===1 && chatTodayBar.height>compactTodayHeight,1000)
                mouseMove(root.contentItem,20,720);cloud.forceTodayBarHover=false
                tryVerify(()=>!chatTodayPhrase.visible && chatTodayBar.height===compactTodayHeight,1000)
                check(!chatTodayPhrase.visible && chatTodayBar.height===compactTodayHeight
                    && chatMoodLabel.text==="good" && chatEnergyLabel.text==="high",
                    "Today Energy/Mood bar did not slide back to compact height and compact pill labels after hover ended")
                Persistent.states.aquaCheckIn.energy="medium";wait(20)
                check(chatEnergyLabel.text==="mid",
                    "medium energy did not abbreviate to mid in compact Today pill: "+chatEnergyLabel.text)
                cloud.forceTodayBarHover=true;wait(40)
                const chatTodayRightCol=root.named(cloud,"aquaChatTodayRightCol")
                check(chatEnergyLabel.text==="Energy: mid"
                    && !!chatTodayRightCol
                    && (chatEnergyBtn.x+chatEnergyBtn.width)<=chatTodayRightCol.width,
                    "medium energy did not render as 'Energy: mid' without clipping on Today hover")
                cloud.forceTodayBarHover=false;Persistent.states.aquaCheckIn.energy="high";wait(30)

                const pomoToggleLabel=root.named(cloud,"aquaUnifiedPomodoroToggleLabel")
                const pomoResetBtn=root.named(cloud,"aquaUnifiedPomodoroReset")
                TimerService.resetPomodoro();wait(20)
                check(!!pomoToggleLabel && pomoToggleLabel.text==="Start" && !!pomoResetBtn && !pomoResetBtn.visible,
                    "Focus bar did not start with Start label and hidden Reset button")
                TimerService.togglePomodoro();wait(30)
                check(pomoToggleLabel.text==="Pause" && !pomoResetBtn.visible,
                    "Focus bar did not switch to Pause while keeping Reset hidden during active focus")
                TimerService.togglePomodoro();wait(220)
                check(pomoToggleLabel.text==="Resume" && pomoResetBtn.visible && pomoResetBtn.enabled,
                    "Pausing Focus did not switch button to Resume and slide Reset button into view")
                TimerService.resetPomodoro();wait(220)
                check(pomoToggleLabel.text==="Start" && !pomoResetBtn.visible,
                    "Resetting Focus did not return button to Start and slide Reset button out")

                // Verify Desktop Agent button is removed from Chatbox (controlled only via Settings) and / Slash Command Menu works directly
                const quickAgentBtn=root.named(cloud,"aquaQuickAgent")
                const agentPanelItem=root.named(cloud,"aquaAgentPanel")
                const slashTriggerBtn=root.named(cloud,"aquaSlashMenuTrigger")
                const slashMenu=root.named(cloud,"aquaSlashCommandMenu")
                const settingsAgentToggle=root.named(settings,"aquaAgentToolsToggle")
                const settingsRagToggle=root.named(settings,"aquaAgentDocRagToggle")
                const settingsMcpToggle=root.named(settings,"aquaAgentMcpToggle")
                const settingsSkillsToggle=root.named(settings,"aquaAgentSkillsToggle")
                check(!quickAgentBtn && !agentPanelItem && !slashTriggerBtn
                    && !!settingsAgentToggle && !!settingsRagToggle && !!settingsMcpToggle && !!settingsSkillsToggle
                    && !!slashMenu && !slashMenu.visible,
                    "Desktop Agent button/panel remained in chatbox or Settings Desktop Agent / Slash Command Menu controls missing")
                cloud.slashMenuDismissed=false;cloud.slashSelectedIndex=0;field.text="/";wait(40)
                check(cloud.profileStage===0 && field.text==="/"
                    && cloud.slashMenuOpen && slashMenu.visible && cloud.filteredSlashCommands.length>=16
                    && (composer.y + composer.height) <= (cloud.height - cloud.bodyTop - 10),
                    "typing / did not open Slash Command Menu or keep composer inside cloud")
                field.text="/le";wait(30)
                check(cloud.slashMenuOpen && cloud.filteredSlashCommands.length>0 && cloud.filteredSlashCommands[0].cmd==="/learn",
                    "typing /le did not filter Slash Command Menu to /learn")
                cloud.selectSlashCommand(cloud.filteredSlashCommands[0],true);wait(30)
                check(field.text==="/learn " && !cloud.slashMenuOpen && !slashMenu.visible,
                    "selecting /learn from Slash Command Menu did not populate composer input and close menu")
                field.text="";cloud.profileStage=0;wait(20)

                const sidebarToggle=root.named(cloud,"aquaChatSidebarToggle")
                const newChatQuick=root.named(cloud,"aquaNewChatButton")
                const sidebarItem=root.named(cloud,"aquaChatSidebar")
                const resizeHandle=root.named(cloud,"aquaSidebarResizeHandle")
                check(!!sidebarToggle && !!newChatQuick && !!sidebarItem && !!resizeHandle && !cloud.sidebarOpen && !resizeHandle.visible,
                    "chat sidebar toggle, resize handle, or quick new chat button missing")
                sidebarToggle.clicked();wait(60)
                check(cloud.sidebarOpen && sidebarItem.visible && resizeHandle.visible && cloud.sidebarWidth>140,
                    "chat sidebar or resize handle did not open when toggled")
                // Verify edge-resizable sidebar width clamping and responsive inner reflow
                cloud.resizeSidebarTo(148);wait(30)
                const defaultThreadTitle=root.named(sidebarItem,"aquaThreadTitle-chat_default")
                check(cloud.sidebarWidth===148 && sidebarItem.width===148 && sidebarItem.isNarrow && sidebarItem.isVeryNarrow
                    && (!defaultThreadTitle || defaultThreadTitle.width>=50),
                    "sidebar did not resize to narrow 148px or active thread title was crushed in narrow mode")
                cloud.resizeSidebarTo(80);wait(20)
                check(cloud.sidebarWidth===cloud.minSidebarWidth,
                    "sidebar resize did not clamp to minSidebarWidth")
                cloud.resizeSidebarTo(236);wait(30)
                check(cloud.sidebarWidth===236 && sidebarItem.width===236 && sidebarItem.isWide && !sidebarItem.isNarrow
                    && AquaMind.customSidebarWidth===236,
                    "sidebar did not resize to wide 236px or persist customSidebarWidth on AquaMind")
                cloud.resizeSidebarTo(186);wait(20)
                AquaMind.createProject("UI Proof Project","/tmp")
                tryVerify(()=>AquaMind.projects.some(p=>p.name==="UI Proof Project") && AquaMind.threadActionQueue.length===0,4000)
                const proofProjId=String(AquaMind.projects.find(p=>p.name==="UI Proof Project")?.id ?? "")
                AquaMind.createProject("Second Proof Project","/tmp")
                tryVerify(()=>AquaMind.projects.some(p=>p.name==="Second Proof Project") && AquaMind.threadActionQueue.length===0,4000)
                const secondProjId=String(AquaMind.projects.find(p=>p.name==="Second Proof Project")?.id ?? "")
                check(!!root.named(sidebarItem,"aquaProjectMove-"+proofProjId),"project reorder button missing in AquaChatSidebar")
                AquaMind.moveProject(proofProjId,0)
                tryVerify(()=>AquaMind.projects[0]?.id===proofProjId && AquaMind.threadActionQueue.length===0,4000)
                AquaMind.createThread("Proof Thread",proofProjId)
                tryVerify(()=>AquaMind.activeThreadTitle==="Proof Thread" && AquaMind.activeProject?.id===proofProjId,4000)
                const proofThreadId=AquaMind.activeThreadId
                const searchInput=root.named(sidebarItem,"aquaSidebarSearchInput")
                const searchClear=root.named(sidebarItem,"aquaSidebarSearchClear")
                check(!!searchInput && !!searchClear && !searchClear.visible,"sidebar search input or clear control missing")
                searchInput.text="preview snippet"
                wait(40)
                check(sidebarItem.searchQuery==="preview snippet" && searchClear.visible
                    && sidebarItem.threadMatchesFilter({id:proofThreadId,title:"Unrelated",preview:"Contains preview snippet here"}),
                    "sidebar search did not match thread preview or reveal clear button")
                searchInput.text=""
                wait(20)
                AquaMind.pendingSudoPassword=true
                AquaMind.pendingDeleteStage=1
                AquaMind.pendingDeletePath="/tmp/stale"
                check(typeof AquaMind.submit==="function" && AquaMind.submit("slow Submit alias check"),"AquaMind.submit alias failed")
                AquaMind.selectThread("chat_default")
                tryVerify(()=>AquaMind.activeThreadId==="chat_default" && !AquaMind.busy && AquaMind.threadActionQueue.length===0,4000)
                check(!AquaMind.pendingSudoPassword && AquaMind.pendingDeleteStage===0 && AquaMind.pendingDeletePath==="",
                    "thread switch did not reset pendingSudoPassword or pendingDeleteStage")
                check(!AquaMind.history.some(row=>String(row.content ?? "").includes("Submit alias check")),
                    "in-flight request leaked into newly selected thread on thread switch")
                AquaMind.selectThread(proofThreadId)
                tryVerify(()=>AquaMind.activeThreadId===proofThreadId && AquaMind.threadActionQueue.length===0,4000)
                sidebarToggle.clicked();wait(40)

                // Verify Flexible Chat Bubble Sizing & Antigravity-Style Dot Navigators
                AquaMind.history=[
                    {id:1,role:"user",content:"hey",created_ms:1700000000000},
                    {id:2,role:"assistant",content:"Hi there! This is a longer assistant reply to verify proportional chat bubble width and Antigravity turn preview card.",created_ms:1700000005000},
                    {id:3,role:"user",content:"Review the shader pipeline",created_ms:1700000060000},
                    {id:4,role:"assistant",content:"Shader pipeline verified with zero warnings.",created_ms:1700000065000}
                ]
                AquaMind.historyLoaded=true
                wait(60)
                const shortBubble=root.named(cloud,"aquaChatBubble-0")
                const longBubble=root.named(cloud,"aquaChatBubble-1")
                check(!!shortBubble && !!longBubble && shortBubble.width<68 && longBubble.width>shortBubble.width+90,
                    "chat bubbles did not flexibly hug content width (shortWidth="+(shortBubble?.width)+", longWidth="+(longBubble?.width)+")")

                // Verify Selectable Chat Bubble Text (mouse/keyboard highlight) & Copy Support
                const bubbleText0=root.named(cloud,"aquaChatBubbleText-0")
                const bubbleText1=root.named(cloud,"aquaChatBubbleText-1")
                const bubbleCopy1=root.named(cloud,"aquaChatBubbleCopy-1")
                check(!!bubbleText0 && !!bubbleText1 && bubbleText0.selectByMouse && bubbleText0.selectByKeyboard
                    && bubbleText0.readOnly && bubbleText0.persistentSelection && !!bubbleCopy1,
                    "chat bubble TextEdit controls or per-bubble copy button missing")
                check(cloud.selectBubbleText(0,0,2)==="he" && bubbleText0.selectedText==="he",
                    "selecting substring in bubble 0 failed")
                check(cloud.selectBubbleText(1,0,8)==="Hi there" && bubbleText1.selectedText==="Hi there"
                    && bubbleText0.selectedText==="" && cloud.activeSelectionBubbleIndex===1 && bubbleCopy1.visible,
                    "selecting substring in bubble 1 did not clear bubble 0 selection or reveal copy button")
                check(cloud.copyActiveSelection() && cloud.lastCopiedText==="Hi there" && cloud.lastCopiedBubbleIndex===1,
                    "copyActiveSelection did not copy highlighted substring 'Hi there' from active bubble 1")
                cloud.lastCopiedText=""
                cloud.lastCopiedBubbleIndex=-1
                root.requestActivate()
                mouseClick(field, 8, 8)
                field.forceActiveFocus()
                wait(20)
                check(field.activeFocus && String(field.selectedText ?? "")==="","composer input did not gain focus with empty selection")
                keyClick(Qt.Key_C,Qt.ControlModifier)
                wait(20)
                check(cloud.lastCopiedText==="Hi there" && cloud.lastCopiedBubbleIndex===1,
                    "Ctrl+C while composer input had activeFocus (with no input selection) did not copy bubble selection")
                bubbleText1.deselect()
                check(cloud.copyBubbleText(0)==="hey" && cloud.lastCopiedText==="hey" && cloud.lastCopiedBubbleIndex===0,
                    "copyBubbleText(0) did not copy full bubble text when no substring was selected")

                // Verify Modern Desktop AI Thinking & Live Drafting Status Card
                const thinkingCard=root.named(cloud,"aquaThinkingCard")
                const thinkingPulse=root.named(cloud,"aquaThinkingPulse")
                const thinkingLabel=root.named(cloud,"aquaThinkingLabel")
                const streamingPreview=root.named(cloud,"aquaStreamingPreview")
                const streamingPreviewText=root.named(cloud,"aquaStreamingPreviewText")
                const thinkingStopBtn=root.named(cloud,"aquaThinkingStopButton")
                check(!!thinkingCard && !thinkingCard.visible && !!thinkingPulse && !!thinkingLabel
                    && !!streamingPreview && !!streamingPreviewText && !!thinkingStopBtn,
                    "Desktop AI thinking/drafting card controls missing")
                check(AquaMind.extractStreamingPreview('{"text":"Path C:\\\\new \\u2014 ready')==="Path C:\\new — ready",
                    "extractStreamingPreview corrupted escaped backslashes or unicode escapes")
                check(AquaMind.extractStreamingPreview('{"text":"<|channel|>analysis internal')==="",
                    "extractStreamingPreview leaked internal protocol token")
                check(AquaMind.sendMessage("slow thinking indicator check"),"slow thinking indicator request rejected")
                wait(35)
                const thinkingRouteBadgeEarly=root.named(cloud,"aquaThinkingRouteBadge")
                check(AquaMind.isWorking && thinkingCard.visible && AquaMind.workPhase==="thinking"
                    && thinkingLabel.text.indexOf("Thinking")===0 && streamingPreview.visible
                    && (thinkingPulse.x + thinkingPulse.width) <= (thinkingLabel.x + 1)
                    && (!thinkingRouteBadgeEarly || !thinkingRouteBadgeEarly.visible || (thinkingLabel.x + thinkingLabel.width) <= (thinkingRouteBadgeEarly.x + 1)),
                    "Desktop AI thinking card did not appear cleanly without header overlap in 'thinking' phase")
                AquaMind.simulatedStreamingText='{"text":"Here is a clean, concise answer'
                wait(25)
                check(AquaMind.workPhase==="drafting" && thinkingLabel.text==="Drafting response..."
                    && AquaMind.streamingPreviewText==="Here is a clean, concise answer"
                    && streamingPreviewText.text==="Here is a clean, concise answer"
                    && streamingPreviewText.selectByMouse,
                    "Desktop AI thinking card did not transition to 'drafting' phase with live streaming preview")
                field.text="/"
                wait(25)
                check(slashMenu.visible && (composer.y+composer.height)<=(cloud.height-cloud.bodyTop-10),
                    "opening slash command menu while thinkingCard is visible pushed composer out of bounds")
                field.text=""
                wait(15)
                keyClick(Qt.Key_Escape)
                wait(20)
                check(AquaMind.isWorking && !AquaMind.conversationOpen && !cloud.panelMode && cloud.visible && cloud.backgroundThinkingBubble,
                    "pressing Escape while Companion is thinking cancelled generation instead of keeping it running in background")
                AquaMind.openChat()
                wait(20)
                check(AquaMind.isWorking && thinkingCard.visible,
                    "reopening chat while Companion is still thinking did not restore thinkingCard")
                thinkingStopBtn.clicked()
                wait(25)
                check(!AquaMind.isWorking && !thinkingCard.visible && AquaMind.simulatedStreamingText==="",
                    "clicking Stop button on Desktop AI thinking card did not cancel generation and hide card")
                AquaMind.history=[
                    {id:1,role:"user",content:"hey",created_ms:1700000000000},
                    {id:2,role:"assistant",content:"Hi there! This is a longer assistant reply to verify proportional chat bubble width and Antigravity turn preview card.",created_ms:1700000005000},
                    {id:3,role:"user",content:"Review the shader pipeline",created_ms:1700000060000},
                    {id:4,role:"assistant",content:"Shader pipeline verified with zero warnings.",created_ms:1700000065000}
                ]
                wait(30)

                const dotNav=root.named(cloud,"aquaChatDotNavigator")
                const dot0=root.named(cloud,"aquaChatDot-0")
                const dot1=root.named(cloud,"aquaChatDot-1")
                const dotPreview=root.named(cloud,"aquaChatDotPreviewCard")
                const dotPrompt=root.named(cloud,"aquaChatDotPreviewPrompt")
                const dotReply=root.named(cloud,"aquaChatDotPreviewReply")
                const dotCounter=root.named(cloud,"aquaChatDotPreviewCounter")
                check(!!dotNav && dotNav.visible && cloud.chatTurns.length===2 && !!dot0 && !!dot1 && !!dotPreview && !dotPreview.visible,
                    "Antigravity chat dot navigator rail or turn dots missing")
                cloud.forcedHoveredTurnIndex=0
                wait(40)
                check(dotPreview.visible && dotPrompt.text==="hey"
                    && dotReply.text.includes("longer assistant reply")
                    && dotCounter.text==="1 of 2",
                    "Antigravity dot hover preview card did not display prompt, reply snippet, and '1 of 2' counter")
                check(cloud.jumpToChatTurn(0) && cloud.activeTurnIndex===0,
                    "clicking/jumping to Antigravity dot turn 0 did not activate turn 0")
                cloud.forcedHoveredTurnIndex=-1
                wait(30)

                // Verify Antigravity dot navigator scaling & clipping on 46-turn thread (no overflow outside transcriptArea)
                const manyMsgs=[]
                for(let t=0;t<46;++t) {
                    manyMsgs.push({id:t*2+1,role:"user",content:"prompt turn "+(t+1),created_ms:1700000000000+t*60000})
                    manyMsgs.push({id:t*2+2,role:"assistant",content:"reply turn "+(t+1),created_ms:1700000005000+t*60000})
                }
                AquaMind.history=manyMsgs
                wait(50)
                check(cloud.chatTurns.length===46 && dotNav.clip && dotNav.height<=dotNav.parent.height,
                    "46-turn dot navigator overflowed transcriptArea (height="+dotNav.height+" vs parent="+dotNav.parent.height+")")
                cloud.forcedHoveredTurnIndex=44
                wait(35)
                check(dotPreview.visible && dotPrompt.text==="prompt turn 45"
                    && dotReply.text.includes("reply turn 45")
                    && dotCounter.text==="45 of 46",
                    "46-turn dot hover preview card did not display '45 of 46' (got '"+dotCounter.text+"')")
                check(cloud.jumpToChatTurn(0) && cloud.activeTurnIndex===0 && cloud.jumpToChatTurn(45) && cloud.activeTurnIndex===45,
                    "46-turn dot navigator failed to jump between first and last turns")
                cloud.forcedHoveredTurnIndex=-1
                wait(25)

                // Verify Two-Tier Small File Editor Popup (+50% width & height sliding expansion)
                const preEditorWidth=cloud.width
                const preEditorHeight=cloud.height
                check(AquaMind.openEditorPopup({
                    filePath:"/tmp/small_widget.py",
                    fileName:"small_widget.py",
                    content:"def calc(x):\n    return x * 2\n",
                    lineCount:2,
                    byteSize:30,
                    language:"py"
                }),"openEditorPopup returned false")
                tryVerify(()=>cloud.editorMode && Math.abs(cloud.editorExpandProgress-1.0)<0.001,1500)
                const editorPopupBox=root.named(cloud,"aquaEditorPopup")
                const editorFileNameLabel=root.named(cloud,"aquaEditorFileName")
                const editorArea=root.named(cloud,"aquaEditorTextArea")
                const editorSaveButton=root.named(cloud,"aquaEditorSaveButton")
                const editorCloseButton=root.named(cloud,"aquaEditorCloseButton")
                const editorExtButton=root.named(cloud,"aquaEditorOpenExternalButton")
                check(!!editorPopupBox && editorPopupBox.visible
                    && cloud.width===Math.round(preEditorWidth*1.5)
                    && cloud.height===Math.round(preEditorHeight*1.5)
                    && editorFileNameLabel.text==="small_widget.py"
                    && editorArea.text.includes("return x * 2")
                    && !!editorSaveButton && !!editorCloseButton && !!editorExtButton,
                    "Editor Popup did not expand Chatbox by +50% (1.5x) or render editor controls (w="+cloud.width+" vs "+Math.round(preEditorWidth*1.5)+", h="+cloud.height+" vs "+Math.round(preEditorHeight*1.5)+")")
                AquaMind.updateEditorContent("def calc(x):\n    return x * 3\n")
                wait(30)
                check(AquaMind.editorDirty && editorFileNameLabel.text==="small_widget.py *",
                    "editing content in Editor Popup did not mark editorDirty")
                AquaMind.closeEditorPopup()
                tryVerify(()=>!cloud.editorMode && Math.abs(cloud.editorExpandProgress)<0.001 && cloud.width===preEditorWidth && cloud.height===preEditorHeight,1500)

                // Verify legacy built-in Eyes/Hands OCR controls are removed in favor of MCP
                settings.visible=true
                wait(30)
                check(!root.named(settings,"aquaAgentScreenOcrToggle") && !root.named(settings,"aquaAgentCaptureApp"),
                    "legacy non-MCP Screen OCR toggle or Capture active app button still present in CompanionConfig")

                // Verify Settings Storage Sub-Tabs (merged Skills, MCP, RAG / State) & Skills list panel
                const tabSkillsBtn=root.named(settings,"aquaStorageTabSkills")
                const tabMcpBtn=root.named(settings,"aquaStorageTabMcp")
                const tabRagBtn=root.named(settings,"aquaStorageTabRag")
                const openSkillsBtn=root.named(settings,"aquaOpenSkillsDirButton")
                const openBuiltinSkillsBtn=root.named(settings,"aquaOpenBuiltinSkillsDirButton")
                const openMcpBtn=root.named(settings,"aquaOpenMcpDirButton")
                const openRagBtn=root.named(settings,"aquaOpenRagDirButton")
                const checkMcpUpdatesBtn=root.named(settings,"aquaCheckMcpUpdatesButton")
                const refreshSkillsBtn=root.named(settings,"aquaRefreshSkillsButton")
                const skillsSection=root.named(settings,"aquaSkillsListSection")
                const mcpSection=root.named(settings,"aquaMcpListSection")
                const ragSection=root.named(settings,"aquaRagListSection")
                const diagSkillCard=root.named(settings,"aquaSkillCard-system-diagnostics")
                const diagSkillToggle=root.named(settings,"aquaSkillToggle-system-diagnostics")
                const diagSkillOpen=root.named(settings,"aquaSkillOpen-system-diagnostics")
                check(!!tabSkillsBtn && !!tabMcpBtn && !!tabRagBtn
                    && !!openSkillsBtn && !openBuiltinSkillsBtn && !!openMcpBtn && !!openRagBtn && !!checkMcpUpdatesBtn && !!refreshSkillsBtn
                    && !!skillsSection && skillsSection.visible && !!mcpSection && !mcpSection.visible && !!ragSection && !ragSection.visible
                    && !!diagSkillCard && !!diagSkillToggle && !!diagSkillOpen,
                    "Companion Settings storage sub-tabs or Skills list cards missing")

                // Switch to MCP sub-tab and verify Eyes & Hands MCP cards, version indicator, and Update button
                tabMcpBtn.clicked()
                wait(40)
                const mcpCardCul=root.named(settings,"aquaMcpCard-computer-use-linux")
                const mcpCardPw=root.named(settings,"aquaMcpCard-playwright-mcp")
                const mcpCardCdt=root.named(settings,"aquaMcpCard-chrome-devtools-mcp")
                const mcpCardDc=root.named(settings,"aquaMcpCard-desktop-commander")
                const mcpCardSys=root.named(settings,"aquaMcpCard-companion-system")
                const mcpVerTextCul=root.named(settings,"aquaMcpVersionText-computer-use-linux")
                const mcpUpdateCul=root.named(settings,"aquaMcpUpdate-computer-use-linux")
                const mcpVerTextPw=root.named(settings,"aquaMcpVersionText-playwright-mcp")
                const mcpUpdatePw=root.named(settings,"aquaMcpUpdate-playwright-mcp")
                const mcpToggleCul=root.named(settings,"aquaMcpToggle-computer-use-linux")
                const mcpOpenCul=root.named(settings,"aquaMcpOpen-computer-use-linux")
                check(mcpSection.visible && !skillsSection.visible
                    && !!mcpCardCul && !!mcpCardPw && !!mcpCardCdt && !!mcpCardDc && !!mcpCardSys
                    && !!mcpVerTextCul && mcpVerTextCul.text==="v1.2.0 → v1.3.0"
                    && !!mcpUpdateCul && mcpUpdateCul.visible
                    && !!mcpVerTextPw && mcpVerTextPw.text==="v0.0.28 → v0.0.31"
                    && !!mcpUpdatePw && mcpUpdatePw.visible
                    && !!mcpToggleCul && !!mcpOpenCul,
                    "MCP sub-tab did not render Eyes & Hands MCP cards, version badge, or Update button")

                // Rapid consecutive clicks on two MCP Update buttons must both succeed (queued without drop)
                mcpUpdateCul.clicked()
                const mcpUpdatePwLive=root.named(settings,"aquaMcpUpdate-playwright-mcp")
                check(!!mcpUpdatePwLive && mcpUpdatePwLive.visible, "playwright-mcp Update button missing after first MCP update")
                mcpUpdatePwLive.clicked()
                wait(40)
                const mcpVerTextCulAfter=root.named(settings,"aquaMcpVersionText-computer-use-linux")
                const mcpUpdateCulAfter=root.named(settings,"aquaMcpUpdate-computer-use-linux")
                const mcpVerTextPwAfter=root.named(settings,"aquaMcpVersionText-playwright-mcp")
                const mcpUpdatePwAfter=root.named(settings,"aquaMcpUpdate-playwright-mcp")
                check(!!mcpVerTextCulAfter && mcpVerTextCulAfter.text==="v1.3.0" && !!mcpUpdateCulAfter && !mcpUpdateCulAfter.visible
                    && !!mcpVerTextPwAfter && mcpVerTextPwAfter.text==="v0.0.31" && !!mcpUpdatePwAfter && !mcpUpdatePwAfter.visible,
                    "rapid consecutive MCP server updates did not update both version badges or hide both Update buttons")
                AquaMind.updateMcpServer("computer-use-linux")
                AquaMind.updateMcpServer("playwright-mcp")
                const mergedStaleMcp=AquaMind._mergeMcpServerVersions([
                    {name:"computer-use-linux",version:"1.2.0",latestVersion:"1.3.0",hasUpdate:true,enabled:true},
                    {name:"playwright-mcp",version:"0.0.28",latestVersion:"0.0.31",hasUpdate:true,enabled:true}
                ])
                check(mergedStaleMcp[0].version==="1.3.0" && mergedStaleMcp[0].hasUpdate===false
                    && mergedStaleMcp[1].version==="0.0.31" && mergedStaleMcp[1].hasUpdate===false,
                    "_mergeMcpServerVersions reverted updated MCP servers back to old versions: "+JSON.stringify(mergedStaleMcp))

                tabRagBtn.clicked()
                wait(25)
                check(ragSection.visible && !mcpSection.visible, "RAG / State sub-tab did not display RAG section")
                tabSkillsBtn.clicked()

                // Verify Dual-Model Auto-Routing, Single-Model plusIQ/Qwen3.5 Mode Switching, and MTP Auto-Optimization UI & Runtime
                const dualRouteGrid=root.named(settings,"aquaDualModelRoutingGrid")
                const settingsAutoRouteToggle=root.named(settings,"aquaAutoModelRoutingToggle")
                const settingsMtpToggle=root.named(settings,"aquaMtpOptimizeToggle")
                const settingsActionSelector=root.named(settings,"aquaActionModelSelector")
                const settingsThinkingSelector=root.named(settings,"aquaThinkingModelSelector")
                const settingsRouteStatus=root.named(settings,"aquaDualModelRouteStatus")
                const pickerAutoRouteToggle=root.named(cloud,"aquaAutoRouteToggle")
                const pickerMtpToggle=root.named(cloud,"aquaMtpOptimizeToggle")
                const pickerRolePrimary=root.named(cloud,"aquaPickerRolePrimary")
                const pickerRoleAction=root.named(cloud,"aquaPickerRoleAction")
                const pickerRoleThinking=root.named(cloud,"aquaPickerRoleThinking")
                const pickerRouteStatus=root.named(cloud,"aquaModelRouteStatus")
                const thinkingRouteBadge=root.named(cloud,"aquaThinkingRouteBadge")
                const thinkingRouteText=root.named(cloud,"aquaThinkingRouteText")
                check(!!dualRouteGrid && !!settingsAutoRouteToggle && !!settingsMtpToggle
                    && !!settingsActionSelector && !!settingsThinkingSelector && !!settingsRouteStatus
                    && !!pickerAutoRouteToggle && !!pickerMtpToggle && !!pickerRolePrimary
                    && !!pickerRoleAction && !!pickerRoleThinking && !!pickerRouteStatus
                    && !!thinkingRouteBadge && !!thinkingRouteText,
                    "Dual-Model Auto-Routing or MTP Auto-Optimization controls missing in CompanionConfig or AquaTalkCloud")

                const plusIqMtpId="DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP"
                Ai.addModel(plusIqMtpId,{
                    name:"Qwen3.5 9B Defiant Fable plusIQ MTP",
                    model:plusIqMtpId,
                    local:true,
                    local_verified:true,
                    local_fingerprint:Quickshell.env("AQUA_TEST_LOCAL_FINGERPRINT"),
                    provider_id:"lmstudio",
                    requires_key:false,
                    api_format:"openai",
                    endpoint:Quickshell.env("AQUA_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported",reasoning:"supported"}
                })
                Ai.modelList=Object.keys(Ai.models)
                AquaMind.setThinkingEffort("high")
                wait(40)
                check(AquaMind.autoModelRoutingEnabled && AquaMind.mtpOptimizeEnabled
                    && AquaMind.dualModelActive
                    && AquaMind.actionModel==="tiny:local"
                    && AquaMind.thinkingModel===plusIqMtpId,
                    "Dual-Model auto-discovery did not pair fast actionModel ('tiny:local') with reasoning thinkingModel ('"+AquaMind.thinkingModel+"')")

                const routeFast=AquaMind.classifyPromptModelRole("bấm vào nút Save trên cửa sổ hiện tại")
                const routeDeep=AquaMind.classifyPromptModelRole("phân tích chuyên sâu kiến trúc hệ thống và tối ưu thuật toán")
                const routeSpoon=AquaMind.classifyPromptModelRole("phân tích triết học và sáng tạo sâu out of the box")
                const routeMixed=AquaMind.classifyPromptModelRole("Click Save and explain why the algorithm deadlocks")
                check(routeFast.role==="action" && routeFast.selectedModel==="tiny:local"
                    && routeDeep.role==="thinking" && routeDeep.selectedModel===plusIqMtpId
                    && routeDeep.isPlusIq && routeDeep.isMtp && routeDeep.mtpActive
                    && routeDeep.inChatDirective==="{REASON:xhigh}"
                    && routeDeep.repetitionPenalty===1.0 && routeDeep.temperature===0.7
                    && routeSpoon.role==="thinking" && routeSpoon.inChatDirective==="{REASON:spoon}"
                    && routeMixed.role==="thinking" && routeMixed.selectedModel===plusIqMtpId,
                    "Dual-Model prompt classification or MTP/plusIQ parameters failed: fast="+JSON.stringify(routeFast)+" deep="+JSON.stringify(routeDeep)+" spoon="+JSON.stringify(routeSpoon)+" mixed="+JSON.stringify(routeMixed))

                // Single-model plusIQ + MTP mode: UI Action -> {REASON:imedium}, Deep Thinking -> {REASON:xhigh}
                AquaMind.selectModel(plusIqMtpId)
                AquaMind.selectActionModel(plusIqMtpId)
                AquaMind.selectThinkingModel(plusIqMtpId)
                settings.width=800
                wait(40)
                const aiGroup=root.named(settings,"aquaAiSettingsGroup")
                const clearBtn=root.named(settings,"aquaClearConversation")
                const resetDialogueBtn=root.named(settings,"aquaResetDialogue")
                const forgetAllBtn=root.named(settings,"aquaForgetAllMemory")
                const clearRightInGroup=clearBtn.mapToItem(aiGroup,clearBtn.width,0).x
                const thinkingComboRightInGroup=settingsThinkingSelector.mapToItem(aiGroup,settingsThinkingSelector.width,0).x
                const resetRightInGroup=resetDialogueBtn.mapToItem(aiGroup,resetDialogueBtn.width,0).x
                const forgetRightInGroup=forgetAllBtn.mapToItem(aiGroup,forgetAllBtn.width,0).x
                const skillToggleRightInGroup=diagSkillToggle.mapToItem(aiGroup,diagSkillToggle.width,0).x
                check(!!aiGroup && aiGroup.width<=settings.width
                    && modelGrid.width<=aiGroup.width
                    && dualRouteGrid.width<=aiGroup.width
                    && quietGrid.width<=aiGroup.width
                    && quotaGrid.width<=aiGroup.width
                    && dialogueGrid.width<=aiGroup.width
                    && skillsSection.width<=aiGroup.width
                    && permGrid.width<=aiGroup.width
                    && clearRightInGroup<=aiGroup.width
                    && thinkingComboRightInGroup<=aiGroup.width
                    && resetRightInGroup<=aiGroup.width
                    && forgetRightInGroup<=aiGroup.width
                    && skillToggleRightInGroup<=aiGroup.width,
                    "Companion AI settings rows or right-edge controls overflowed aiGroup width with long model name & thinking effort enabled: "
                    +JSON.stringify({
                        settingsW:settings.width,aiGroupW:aiGroup ? aiGroup.width : -1,
                        modelGridW:modelGrid.width,dualRouteGridW:dualRouteGrid.width,
                        quietGridW:quietGrid.width,quotaGridW:quotaGrid.width,
                        dialogueGridW:dialogueGrid.width,skillsW:skillsSection.width,permGridW:permGrid.width,
                        clearRightX:clearRightInGroup,thinkingComboRightX:thinkingComboRightInGroup,
                        resetRightX:resetRightInGroup,forgetRightX:forgetRightInGroup,skillToggleRightX:skillToggleRightInGroup
                    }))
                const singleFast=AquaMind.classifyPromptModelRole("click the submit button")
                const singleDeep=AquaMind.classifyPromptModelRole("/plan design a lock-free ring buffer")
                check(AquaMind.model===plusIqMtpId
                    && !AquaMind.dualModelActive && AquaMind.currentModelIsPlusIq && AquaMind.currentModelIsMtp && AquaMind.mtpActive
                    && singleFast.role==="action" && singleFast.inChatDirective==="{REASON:imedium}" && singleFast.recommendedEffort==="off"
                    && singleFast.repetitionPenalty===1.0 && singleFast.temperature===0.7
                    && singleDeep.role==="thinking" && singleDeep.inChatDirective==="{REASON:xhigh}" && singleDeep.recommendedEffort==="high",
                    "Single-model plusIQ + MTP auto-switching failed: model="+AquaMind.model+" singleFast="+JSON.stringify(singleFast)+" singleDeep="+JSON.stringify(singleDeep))

                // Verify Ai.buildChatRequest MTP optimization on vs off, and memoryContext targetModelObj privacy guard
                const testStrat=Ai.createStrategy(root,"openai")
                const mtpReqOn=Ai.buildChatRequest(Ai.models[plusIqMtpId],testStrat,[{role:"user",content:"test"}],"sys",0.8,[],"","off")
                check(mtpReqOn.repetition_penalty===1.0 && mtpReqOn.repeat_penalty===1.0 && mtpReqOn.temperature<=0.7,
                    "Ai.buildChatRequest did not apply MTP repetition_penalty=1.0 and temperature<=0.7 when mtpOptimizeEnabled is true")

                // Toggle MTP optimization off and back on
                AquaMind.setMtpOptimizeEnabled(false)
                wait(20)
                const singleMtpOff=AquaMind.classifyPromptModelRole("click the submit button")
                const mtpReqOff=Ai.buildChatRequest(Ai.models[plusIqMtpId],testStrat,[{role:"user",content:"test"}],"sys",0.8,[],"","off")
                check(!AquaMind.mtpOptimizeEnabled && !AquaMind.mtpActive && singleMtpOff.repetitionPenalty===null
                    && mtpReqOff.repetition_penalty===undefined && mtpReqOff.repeat_penalty===undefined,
                    "Disabling mtpOptimizeEnabled did not turn off MTP parameter optimization")
                check(AquaMind.memoryContext(Date.now(), {local:false, local_verified:false}).length===0,
                    "memoryContext leaked memories when targetModelObj is non-local")
                const settingsRefreshBtn=root.named(settings,"aquaRefreshModelsButton")
                const pickerRefreshBtn=root.named(cloud,"aquaPickerRefreshModelsButton")
                check(!!settingsRefreshBtn && !!pickerRefreshBtn,
                    "Auto-detect refresh buttons missing in settings or talk cloud model picker")
                Config.setNestedValues({
                    "abyss.companionMind.model":"lmstudio-lfm2.5-vl-3b",
                    "abyss.companionMind.actionModel":"lmstudio-qwen3.5-4b-mtp",
                    "abyss.companionMind.thinkingModel":"gguf:deleted123"
                })
                AquaMind.reconcileAvailableModels()
                wait(20)
                check(String(Config.options.abyss.companionMind.model ?? "")===""
                    && String(Config.options.abyss.companionMind.actionModel ?? "")===""
                    && String(Config.options.abyss.companionMind.thinkingModel ?? "")===""
                    && AquaMind.available,
                    "reconcileAvailableModels did not purge deleted local models or restore active model")
                // Verify:
                // 1) Long model name + effort selector + send button alignment inside composer
                // 2) Multi-level thinking effort (6 tiers) even when LM Studio reports reasoning:"unknown" and reasoningOff:"supported"
                // 3) Deduplication of ~/.lmstudio/models GGUF entry when lmstudio-* is runnable
                Ai.models[plusIqMtpId].capabilities = {chat:"supported", reasoning:"unknown", reasoningOff:"supported"}
                const dupGgufId = "gguf:daa5831977714b5ecb64"
                Ai.addModel(dupGgufId, {
                    name:"Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M",
                    model:dupGgufId,
                    local:true,
                    free:true,
                    provider_id:"local-gguf",
                    api_format:"gguf",
                    gguf_path:"/home/user/.lmstudio/models/DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF/Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-MTP-Q4_K_M.gguf",
                    runtime_path:"/usr/bin/llama-cli",
                    capabilities:{chat:"supported", reasoning:"supported"}
                })
                Ai.modelList = Object.keys(Ai.models)
                AquaMind.selectModel(plusIqMtpId)
                AquaMind.openChat()
                wait(40)
                const sendControl = root.named(cloud, "aquaChatSend")
                const sendRightInComposer = sendControl.mapToItem(composer, sendControl.width, sendControl.height / 2)
                const modelCenterInComposer = modelSelector.mapToItem(composer, modelSelector.width / 2, modelSelector.height / 2)
                check(!!sendControl
                    && sendRightInComposer.x <= composer.width - 2
                    && Math.abs(sendRightInComposer.y - modelCenterInComposer.y) <= 2
                    && modelSelector.x + modelSelector.width <= profileButton.x
                    && profileButton.x + profileButton.width <= sendControl.x,
                    "Send button or model/effort pills overflowed or misaligned in composer with long model name: "
                    + JSON.stringify({sendRightX:sendRightInComposer.x, composerW:composer.width, sendY:sendRightInComposer.y, modelY:modelCenterInComposer.y}))
                check(AquaMind.reasoningSupported && AquaMind.instantSupported && AquaMind.thinkingSupported
                    && AquaMind.thinkingLevels.length === 6
                    && AquaMind.thinkingLevels.map(e => e.value).join(",") === "instant,low,medium,high,xhigh,max",
                    "Multi-level thinking effort tiers not exposed for Qwen3.5/plusIQ model when reasoning capability was 'unknown': "
                    + JSON.stringify(AquaMind.thinkingLevels))
                check(!AquaMind.selectableModels.some(m => m.name === dupGgufId)
                    && AquaMind.selectableModels.some(m => m.name === plusIqMtpId),
                    "Duplicate ~/.lmstudio/models GGUF entry was not deduplicated from selectableModels: "
                    + JSON.stringify(AquaMind.selectableModels))
                // Verify any custom/unrecognized model name that advertises reasoningOff:"supported" or thinking:true automatically unlocks all 6 Thinking Effort tiers
                const genericReasonId = "custom-alpha-7b"
                Ai.addModel(genericReasonId, {
                    name:"Custom Alpha 7B",
                    model:genericReasonId,
                    local:true,
                    local_verified:true,
                    local_fingerprint:Quickshell.env("AQUA_TEST_LOCAL_FINGERPRINT"),
                    provider_id:"lmstudio",
                    requires_key:false,
                    api_format:"openai",
                    endpoint:Quickshell.env("AQUA_TEST_ENDPOINT")+"/v1/chat/completions",
                    capabilities:{chat:"supported", reasoning:"unknown", reasoningOff:"supported"}
                })
                Ai.modelList = Object.keys(Ai.models)
                AquaMind.selectModel(genericReasonId)
                wait(25)
                check(AquaMind.reasoningSupported && AquaMind.instantSupported && AquaMind.thinkingSupported
                    && AquaMind.thinkingLevels.length === 6
                    && AquaMind.thinkingLevels.map(e => e.value).join(",") === "instant,low,medium,high,xhigh,max",
                    "Custom model with reasoningOff:'supported' did not automatically unlock all 6 Thinking Effort tiers: "
                    + JSON.stringify(AquaMind.thinkingLevels))
                delete Ai.models[genericReasonId]
                delete Ai.models[dupGgufId]
                AquaMind.setMtpOptimizeEnabled(true)
                AquaMind.selectActionModel("")
                AquaMind.selectThinkingModel("")
                AquaMind.selectModel("tiny:local")
                AquaMind.setThinkingEffort("off")
                delete Ai.models[plusIqMtpId]
                Ai.modelList=Object.keys(Ai.models)
                settings.visible=false
                wait(25)

                // Verify Interactive Clarification Card (Codex / Antigravity / Claude Desktop style: 1-3 selectable ideas + 1 custom input box)
                AquaMind.openChat()
                wait(30)
                check(AquaMind.sendMessage("/clarify optimize companion performance"),
                    "/clarify command did not trigger interactive clarification")
                wait(40)
                const clarCard = root.named(cloud, "aquaClarificationCard")
                const clarQuestion = root.named(cloud, "aquaClarificationQuestion")
                const clarOpt0 = root.named(cloud, "aquaClarificationOption-0")
                const clarOpt1 = root.named(cloud, "aquaClarificationOption-1")
                const clarOpt2 = root.named(cloud, "aquaClarificationOption-2")
                const clarRec0 = root.named(cloud, "aquaClarificationRecommended-0")
                const clarCustomInput = root.named(cloud, "aquaClarificationCustomInput")
                const clarCustomSubmit = root.named(cloud, "aquaClarificationCustomSubmit")
                check(!!clarCard && clarCard.visible
                    && !!clarQuestion && clarQuestion.text.length > 0
                    && !!clarOpt0 && clarOpt0.visible
                    && !!clarOpt1 && clarOpt1.visible
                    && !!clarOpt2 && clarOpt2.visible
                    && !!clarRec0 && clarRec0.visible
                    && !!clarCustomInput && clarCustomInput.visible
                    && !!clarCustomSubmit && clarCustomSubmit.visible
                    && clarCard.y + clarCard.height <= composer.y + 1,
                    "Interactive Clarification Card did not render 1-3 options + custom input cleanly above composer: "
                    + JSON.stringify({
                        cardVisible: clarCard ? clarCard.visible : false,
                        optCount: AquaMind.activeClarification?.options?.length ?? 0,
                        cardBottom: clarCard ? clarCard.y + clarCard.height : -1,
                        composerY: composer.y
                    }))

                // Click Idea Option #1 and verify it resolves clarification & sends the combined prompt
                clarOpt0.clicked()
                wait(220)
                tryCompare(AquaMind, "busy", false, 4000)
                check(AquaMind.activeClarification === null
                    && AquaMind.history.some(m => m.role === "user" && m.content.includes("optimize companion performance")),
                    "Clicking clarification option #1 did not clear activeClarification or dispatch clarified prompt")

                // Pose clarification via LLM reply extraction (1-3 numbered ideas) and submit custom idea via custom input box
                const parsedReplyClar = AquaMind.extractClarificationFromReply(
                    "Which architecture would you prefer for the new module?\n1. Event-driven pipeline (Recommended) — Low latency and decoupled\n2. Polling worker — Simple periodic checks\n3. Hybrid state machine — Combines reactive signals with fallback timer",
                    "Design the new module"
                )
                check(!!parsedReplyClar && parsedReplyClar.options.length === 3 && parsedReplyClar.options[0].recommended === true,
                    "extractClarificationFromReply failed to parse 3 numbered ideas from assistant reply")
                AquaMind.activeClarification = parsedReplyClar
                wait(40)
                check(clarCard.visible, "Clarification card did not appear for extracted LLM clarification")
                clarCustomInput.text = "Use a lock-free ring buffer with reactive wakeup"
                wait(20)
                check(clarCustomSubmit.enabled, "Custom clarification submit button did not enable when custom text entered")
                clarCustomSubmit.clicked()
                wait(220)
                check(AquaMind.activeClarification === null
                    && AquaMind.history.some(m => m.role === "user" && m.content.includes("Use a lock-free ring buffer with reactive wakeup")),
                    "Submitting custom clarification idea did not dispatch combined custom prompt")
                tryCompare(AquaMind, "busy", false, 4000)

                // Verify:
                // 1) Compact Horizontal Stepped Slider for Thinking Effort (saves vertical space: <= 40px)
                // 2) Custom Display Name (Alias) for models with long names (in both Chatbox Model Picker and Settings)
                // 3) Image attachment in Chatbox (Attach button, preview strip, remove button, send with image, bubble thumbnail)
                Ai.models["tiny:local"].capabilities = {chat:"supported", reasoning:"supported"}
                wait(30)
                cloud.profileStage = 1
                wait(30)
                const effortTrack = root.named(cloud, "aquaThinkingEffortSliderTrack")
                const effortLowBtn = root.named(cloud, "aquaEffortOption-low")
                const effortMaxBtn = root.named(cloud, "aquaEffortOption-max")
                check(effortSlider.visible && effortSlider.height <= 40 && !!effortTrack
                    && !!effortLowBtn && !!effortMaxBtn && effortMaxBtn.x > effortLowBtn.x,
                    "Thinking effort did not render as a compact horizontal stepped slider: "
                    + JSON.stringify({h:effortSlider.height, lowX:effortLowBtn?.x, maxX:effortMaxBtn?.x}))

                // Verify custom model display name is configured exclusively in Settings (collapsed behind icon button) and compact Chatbox Model Picker
                cloud.profileStage = 2
                wait(30)
                const pickerCustomBtn = root.named(cloud, "aquaPickerRoleCustom")
                const autoModelOptionLabel = root.named(cloud, "aquaModelLabel-")
                check(!root.named(cloud, "aquaModelRenameRow") && !root.named(cloud, "aquaModelAliasInput")
                    && !!pickerCustomBtn && pickerCustomBtn.visible
                    && !pickerRoleAction.visible && !pickerRoleThinking.visible
                    && !!autoModelOptionLabel && autoModelOptionLabel.text === "Automatic local model",
                    "Compact Chatbox modelPickerPanel did not hide UI Action / Deep Thinking behind Custom or strip model suffix from Automatic local model")
                pickerCustomBtn.clicked()
                wait(25)
                check(pickerRoleAction.visible && pickerRoleThinking.visible && cloud.modelPickerRole === "action",
                    "Clicking Custom in modelPickerPanel did not reveal UI Action and Deep Thinking buttons")
                pickerRolePrimary.clicked()
                wait(20)
                check(!pickerRoleAction.visible && !pickerRoleThinking.visible && cloud.modelPickerRole === "primary",
                    "Clicking Auto in modelPickerPanel did not collapse UI Action and Deep Thinking buttons")
                settings.visible = true
                wait(30)
                const toggleAliasBtn = root.named(settings, "aquaToggleModelAliasButton")
                const renameRow = root.named(settings, "aquaSettingsModelAliasRow")
                const aliasInput = root.named(settings, "aquaSettingsModelAliasInput")
                const aliasSave = root.named(settings, "aquaSettingsModelAliasSave")
                const aliasReset = root.named(settings, "aquaSettingsModelAliasReset")
                const mcpFlickable = root.named(settings, "aquaMcpListFlickable")
                check(!!toggleAliasBtn && toggleAliasBtn.visible && !!renameRow && !renameRow.visible
                    && !!aliasInput && !!aliasSave && !!aliasReset && !!mcpFlickable,
                    "Model display name icon button or collapsed rename row missing in Settings (CompanionConfig)")
                toggleAliasBtn.clicked()
                wait(25)
                check(renameRow.visible, "Clicking aquaToggleModelAliasButton did not expand settingsModelAliasRow")
                aliasInput.text = "Qwen 3.5 Pro"
                aliasSave.clicked()
                wait(30)
                check(!renameRow.visible
                    && AquaMind.modelAliasFor("tiny:local") === "Qwen 3.5 Pro"
                    && AquaMind.currentModelLabel === "Qwen 3.5 Pro"
                    && root.named(cloud, "aquaModelLabel-tiny:local")?.text === "Qwen 3.5 Pro",
                    "Saving custom model display name in Settings did not update currentModelLabel, collapse row, and update Chatbox model picker option label: "
                    + AquaMind.currentModelLabel)
                toggleAliasBtn.clicked()
                wait(25)
                check(renameRow.visible && aliasReset.visible,
                    "Re-opening settingsModelAliasRow did not show reset button")
                aliasReset.clicked()
                toggleAliasBtn.clicked()
                wait(25)
                check(!renameRow.visible
                    && AquaMind.modelAliasFor("tiny:local") === ""
                    && AquaMind.currentModelLabel === "Tiny fixture",
                    "Resetting custom model display name in Settings did not restore original model label")
                settings.visible = false
                cloud.profileStage = 0
                wait(20)

                // Verify Image Attachment in Chatbox
                const attachBtn = root.named(cloud, "aquaChatAttachImage")
                const previewRow = root.named(cloud, "aquaChatImagePreviewRow")
                check(!!attachBtn && attachBtn.visible && !!previewRow && !previewRow.visible,
                    "Chatbox attach image button or preview row missing")
                check(AquaMind.attachImage("/tmp/companion-shot-1.png")
                    && AquaMind.attachImage("/tmp/companion-shot-2.jpg")
                    && AquaMind.attachedImages.length === 2,
                    "AquaMind.attachImage failed to attach PNG/JPG images")
                wait(30)
                const imgCard0 = root.named(cloud, "aquaAttachedImageCard-0")
                const imgRemove1 = root.named(cloud, "aquaAttachedImageRemove-1")
                check(previewRow.visible && !!imgCard0 && !!imgRemove1 && root.named(cloud, "aquaChatSend").enabled,
                    "Attached image preview cards did not appear or enable Send button when text input is empty")
                imgRemove1.clicked()
                wait(20)
                check(AquaMind.attachedImages.length === 1 && AquaMind.attachedImages[0].name === "companion-shot-1.png",
                    "Removing attached image #1 did not leave 1 image in attachedImages")
                root.named(cloud, "aquaChatSend").clicked()
                wait(40)
                tryCompare(AquaMind, "busy", false, 4000)
                const imgHistoryIdx = AquaMind.history.findIndex(m => m.role === "user" && Array.isArray(m.images) && m.images.length === 1)
                check(AquaMind.attachedImages.length === 0 && imgHistoryIdx >= 0,
                    "Sending message with attached image did not clear attachedImages and record images on user history entry")

                // Verify:
                // 1) Merged 1-row, 2-column layout for Focus (Pomodoro) & Today Mood/Energy
                // 2) Icon-only Model Selector button (30x30) + crisp centered Effort Selector button
                // 3) Thinking Effort slider drag (applyEffortAtX) + animated thumb
                // 4) Assistant image reply rendering in Chatbox (e.g. screenshot reply)
                const todayRightCol = root.named(cloud, "aquaChatTodayRightCol")
                const effortBtnLabel = root.named(cloud, "aquaModelEffortLabel")
                const effortThumb = root.named(cloud, "aquaThinkingEffortThumb")
                AquaMind.setThinkingEffort("xhigh")
                wait(30)
                check(!!todayRightCol && unifiedPomodoroBar.visible && todayRightCol.visible
                    && unifiedPomodoroBar.y === 0 && todayRightCol.y === 0
                    && todayRightCol.x >= unifiedPomodoroBar.width + 4
                    && Math.abs(unifiedPomodoroBar.width - todayRightCol.width) <= 4,
                    "Focus and Today Mood/Energy were not merged into 1 row, 2 equal columns: "
                    + JSON.stringify({pomoW:unifiedPomodoroBar.width, rightX:todayRightCol?.x, rightW:todayRightCol?.width}))
                check(modelSelector.width === 30 && modelSelector.height === 30
                    && !!effortBtnLabel && effortBtnLabel.visible && effortBtnLabel.text === "XHigh"
                    && effortBtnLabel.width >= 20 && profileButton.width >= 68,
                    "Model selector is not icon-only (30x30) or Effort selector label collapsed: "
                    + JSON.stringify({modelW:modelSelector.width, effortW:profileButton.width, labelText:effortBtnLabel?.text, labelW:effortBtnLabel?.width}))
                cloud.profileStage = 1
                wait(30)
                check(!!effortThumb && effortThumb.visible
                    && effortSlider.applyEffortAtX(effortSlider.width - 4, false)
                    && AquaMind.thinkingEffort === "max"
                    && effortSlider.applyEffortAtX(4, true)
                    && (AquaMind.thinkingEffort === "instant" || AquaMind.thinkingEffort === "off")
                    && cloud.profileStage === 0,
                    "Dragging Thinking Effort slider (applyEffortAtX) did not update effort and close slider popup")
                check(AquaMind.shouldRouteToAgentTurn("chụp màn hình giúp tôi", true)
                    && AquaMind.shouldRouteToAgentTurn("take a screenshot", true)
                    && AquaMind.extractReplyImages("Here is the shot ![screen](/tmp/screen-capture.png)").length === 1,
                    "Screenshot prompt routing or extractReplyImages failed")
                AquaMind.history = [
                    {
                        id: 998,
                        role: "user",
                        content: "chụp màn hình giúp tôi"
                    },
                    {
                        id: 999,
                        role: "assistant",
                        content: "📷 Đã chụp màn hình (`companion-shot-1.png`).\n\n![screenshot](/tmp/companion-shot-1.png)",
                        images: ["/tmp/companion-shot-1.png"]
                    }
                ]
                wait(40)
                const assistantImgIdx = 1
                const assistantImgRow = root.named(cloud, "aquaBubbleImages-" + assistantImgIdx)
                const assistantImgThumb = root.named(cloud, "aquaBubbleImageThumb-" + assistantImgIdx + "-0")
                check(!!assistantImgRow && assistantImgRow.visible
                    && !!assistantImgThumb && assistantImgThumb.height === 122 && assistantImgThumb.width >= 120,
                    "Assistant image reply card did not render inside chat bubble: "
                    + JSON.stringify({rowVis:assistantImgRow?.visible, thumbW:assistantImgThumb?.width, thumbH:assistantImgThumb?.height}))

                // Verify:
                // 5) Focus Reload (Reset) button alignment when paused inside unifiedPomodoroBar
                // 6) Auto-unload idle LLM setting in CompanionConfig (NOT in AquaTalkCloud)
                // 7) Desktop Companion thinking animation + 3-dot status speech bubble when chatbox is closed while thinking
                TimerService.pomodoroRunning = true
                TimerService.pomodoroPaused = true
                wait(220)
                const pomoResetBtnAligned = root.named(cloud, "aquaUnifiedPomodoroReset")
                const pomoToggleLabelAligned = root.named(cloud, "aquaUnifiedPomodoroToggleLabel")
                check(!!pomoResetBtnAligned && pomoResetBtnAligned.visible && pomoResetBtnAligned.parent.visible
                    && pomoToggleLabelAligned?.text === "Resume"
                    && (pomoResetBtnAligned.parent.x + pomoResetBtnAligned.parent.width) <= unifiedPomodoroBar.width - 4
                    && Math.abs((unifiedPomodoroToggle.y + unifiedPomodoroToggle.height / 2) - (pomoResetBtnAligned.parent.y + pomoResetBtnAligned.parent.height / 2)) <= 1,
                    "Focus Reload (Reset) button overflowed unifiedPomodoroBar or was misaligned when paused: "
                    + JSON.stringify({resetRight: (pomoResetBtnAligned?.parent?.x ?? 0) + (pomoResetBtnAligned?.parent?.width ?? 0), barW: unifiedPomodoroBar.width}))
                TimerService.resetPomodoro()
                wait(40)

                const settingsUnloadCombo = root.named(settings, "aquaLlmIdleUnloadSelector")
                const chatUnloadCombo = root.named(cloud, "aquaLlmIdleUnloadSelector")
                check(!!settingsUnloadCombo && !chatUnloadCombo
                    && AquaMind.setLlmIdleUnloadMinutes(15) && AquaMind.llmIdleUnloadMinutes === 15,
                    "Auto-unload idle LLM selector missing in Settings or exposed in Chatbox")
                AquaMind.llmModelLoadedInMemory = true
                check(AquaMind.unloadIdleLlm() && !AquaMind.llmModelLoadedInMemory && AquaMind.llmUnloadCount >= 1,
                    "AquaMind.unloadIdleLlm() did not unload idle LLM state")

                AquaMind.dismiss()
                wait(40)
                AquaMind.pending = {action: "ai_chat", serial: 999999, request: {}}
                AquaMind.busy = true
                wait(60)
                const bgDotsBubble = root.named(cloud, "aquaBackgroundThinkingDotsBubble")
                const bgDotsRow = root.named(cloud, "aquaBackgroundThinkingDotsRow")
                check(!AquaMind.conversationOpen && cloud.visible && cloud.backgroundThinkingBubble
                    && !!bgDotsBubble && bgDotsBubble.visible && !!bgDotsRow
                    && cloud.width <= 72 && cloud.height <= 40
                    && actor.faceExpression === "thinking" && actor.motionAction === "inspect",
                    "Closed chatbox while Companion is thinking did not show 3-dot speech bubble and thinking animation: "
                    + JSON.stringify({vis:cloud.visible, bgBubble:cloud.backgroundThinkingBubble, w:cloud.width, h:cloud.height, expr:actor.faceExpression, motion:actor.motionAction}))
                AquaMind.busy = false
                AquaMind.pending = null
                wait(40)

                Ai.models["tiny:local"].capabilities = {chat:"supported", reasoning:"unsupported"}

                AquaMind.dismiss()
                embeddedView.visible=true;wait(60)
                check(AquaMind.sidebarChatOpen && embeddedView.cloud.embeddedMode && !!embeddedView.inputField,
                    "unified AquaCompanionChatView did not activate or expose inputField")
                embeddedView.visible=false;wait(40)
                check(!AquaMind.sidebarChatOpen,"AquaCompanionChatView did not release sidebarChatOpen on hide")
                console.log("AQUA_MIND=PASS actualProcess sharedModelReadiness EnglishReply hoverCloudActions dailyChoices readOnlyContext borderedCloud noCheckInInput separateQuestions journalWrites stableJournalContext explicitChatFocus compactTodayHover modelEffortSelector compactEffortRow activeModelContrast stagedEffortModelPicker slashCommandMenu alignedAgentPanel enterSend sendOnlyControl persistentHistory reminderSources weeklyPlannerEvents noJournalReminders compactAiSettings proactiveCadences scaledTalkCloudAnchor idleChatRelease retainedDraft escapeClose boundedHistory cancel staleReply invalidEndpoint unavailableModel settingsAI noReferenceVault explicitMemory save edit castScope revocation forget managementControls chatProjectSidebar resizableSidebar unifiedCompanionChatView flexibleChatBubbles selectableChatBubbles desktopAiThinkingCard antigravityDotNavigator twoTierEditorPopup storageAndSkillsSettings dualModelAutoRouting plusIqModeSwitching mtpAutoOptimization autoDetectAvailableModels")
            } catch(e) {console.error("AQUA_MIND=FAIL "+e+" "+e.stack)}
            shutdown.start()
        }
    }
    Timer {interval:200;running:Config.ready;onTriggered:input.runChecks()}
    Timer {id:shutdown;interval:150;onTriggered:Qt.quit()}
}
