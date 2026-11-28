"""Emails sortants verrouillés (audit SEC-05 et SEC-06) : SMTP simulé, aucun envoi réel.

- le mode test est revérifié par l'exécuteur SMTP, quel que soit le chemin suivi ;
- la pièce jointe validée est figée (copie + SHA-256) : ce qui part est exactement ce qui a été validé ;
- la livraison doit contenir la version exacte relue par le chef de projet.
"""
import json
import os
import unittest.mock
import zipfile
from email import message_from_bytes
from pathlib import Path

from ai_studio import approvals, commerce
from ai_studio.agents import ROLES
from ai_studio.outgoing import frozen_dir
from ai_studio.tools import ToolContext, run_tool
from test_commerce import Base

REPORT = "# Rapport\n" + "Constat détaillé et sourcé (https://www.ax-07.fr/, consulté le 28/11/2026). " * 20


class FakeSMTP:
    sent: list = []

    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): pass
    def login(self, *a): pass
    def send_message(self, msg): FakeSMTP.sent.append(msg)


class OutgoingTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        FakeSMTP.sent = []
        p = unittest.mock.patch.object(approvals.smtplib, "SMTP", FakeSMTP)
        p.start(); self.addCleanup(p.stop)
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def ctx(self, agent="support"):
        tid = self.db.add_task("t", agent)
        return ToolContext(self.cfg, self.db, agent, self.db.task(tid), list(ROLES))

    def set_test_mode(self, *emails):
        self.cfg.data.setdefault("company", {})["test_mode"] = {"enabled": True, "recipients": list(emails)}

    def order_with_report(self, email="marie@cafe-lumiere.fr", name="rapport.md"):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", email)
        d = self.cfg.workspace / "livraisons" / f"commande-{n}"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(REPORT, encoding="utf-8")
        return n, f"livraisons/commande-{n}/{name}"

    def review(self, n, rel):
        out = run_tool("confirm_content_review", self.ctx("chef_projet"),
                       {"order": n, "file": rel, "checked": "Rapport relu en entier : 10 actions, sources ouvertes une à une.", "corrections": "aucune"})
        self.assertIn("Relecture enregistrée", out)

    def deliver(self, n, rel):
        return run_tool("deliver_order", self.ctx("support"), {"order": n, "attachment": rel, "subject": "Votre rapport", "body": "Bonjour"})

    def attachment_of(self, msg):
        parts = [p for p in message_from_bytes(msg.as_bytes()).walk() if p.get_filename()]
        return parts[0].get_payload(decode=True) if parts else None

    # ------------------------------------------------------------ SEC-05 : mode test
    def test_executor_blocks_unauthorized_recipient_whatever_the_path(self):
        self.set_test_mode("moi@exemple.fr")
        aid = self.db.add_approval("support", None, "send_email", {"to": "vrai.client@societe.fr", "subject": "s", "body": "b"}, "r")
        out = approvals.decide(self.cfg, self.db, aid, True)
        self.assertIn("échoué", out); self.assertIn("mode test", out)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertEqual(self.db.approval(aid)["status"], "error")

    def test_owner_cannot_redirect_to_unauthorized_recipient_in_test_mode(self):
        self.set_test_mode("moi@exemple.fr", "autre@exemple.fr")
        aid = self.db.add_approval("support", None, "send_email", {"to": "moi@exemple.fr", "subject": "s", "body": "b"}, "r")
        with self.assertRaises(ValueError):
            approvals.decide(self.cfg, self.db, aid, True, edits={"to": "vrai.client@societe.fr"})
        self.assertEqual(self.db.approval(aid)["status"], "pending")         # rien n'est parti, la demande reste en attente
        self.assertEqual(FakeSMTP.sent, [])
        self.assertIn("exécuté", approvals.decide(self.cfg, self.db, aid, True, edits={"to": "autre@exemple.fr"}))
        self.assertEqual(FakeSMTP.sent[0]["To"], "autre@exemple.fr")

    def test_delivery_refused_in_test_mode_for_real_customer(self):
        n, rel = self.order_with_report(email="vrai.client@societe.fr")
        self.set_test_mode("moi@exemple.fr")
        self.review(n, rel)
        out = self.deliver(n, rel)
        self.assertIn("mode test", out)
        self.assertEqual(self.db.pending_approvals(), [])

    # ------------------------------------------------------------ SEC-06 : pièce jointe figée
    def test_attachment_modified_after_submission_is_not_sent(self):
        n, rel = self.order_with_report()
        self.review(n, rel)
        self.assertIn("en attente de validation", self.deliver(n, rel))
        a = self.db.pending_approvals()[0]
        payload = json.loads(a["payload"])
        self.assertEqual(len(payload["attachment_sha256"]), 64)
        original = (self.cfg.workspace / rel).read_bytes()                  # octets réels (\r\n sous Windows)
        (self.cfg.workspace / rel).write_text("VERSION MODIFIÉE PAR UN AGENT", encoding="utf-8")   # après la validation demandée
        from ai_studio.server import deliverable_preview
        self.assertEqual(deliverable_preview(self.cfg, self.db, a["id"])[0], original)   # l'aperçu montre la version figée
        self.assertIn("exécuté", approvals.decide(self.cfg, self.db, a["id"], True))
        self.assertEqual(self.attachment_of(FakeSMTP.sent[0]), original)                  # c'est elle qui part

    def test_tampered_frozen_copy_blocks_sending(self):
        n, rel = self.order_with_report()
        self.review(n, rel)
        self.deliver(n, rel)
        a = self.db.pending_approvals()[0]
        frozen = next(frozen_dir(self.cfg).rglob("rapport.md"))
        os.chmod(frozen, 0o644)
        frozen.write_text("falsifié", encoding="utf-8")
        out = approvals.decide(self.cfg, self.db, a["id"], True)
        self.assertIn("modifiée depuis la validation", out)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertNotEqual(commerce.get_order(self.db, n)["stage"], "livree")

    def test_frozen_copy_is_outside_agent_workspace(self):
        n, rel = self.order_with_report()
        self.review(n, rel)
        self.deliver(n, rel)
        frozen = next(frozen_dir(self.cfg).rglob("rapport.md"))
        self.assertNotIn(self.cfg.workspace.resolve(), frozen.resolve().parents)
        self.assertIn("hors de l'espace de travail",
                      run_tool("write_file", self.ctx("atelier"), {"path": "../pieces_validees/x.md", "content": "x"}))

    def test_legacy_request_without_fingerprint_is_not_sent(self):
        n, rel = self.order_with_report()
        aid = self.db.add_approval("support", None, "send_email",
                                   {"to": "marie@cafe-lumiere.fr", "subject": "s", "body": "b", "attachment": rel, "order": n}, "r")
        out = approvals.decide(self.cfg, self.db, aid, True)
        self.assertIn("non figée", out)
        self.assertEqual(FakeSMTP.sent, [])

    def test_send_email_tool_freezes_attachment_too(self):
        self.cfg.workspace.joinpath("docs").mkdir(parents=True, exist_ok=True)
        self.cfg.workspace.joinpath("docs/plaquette.md").write_text("Plaquette v1", encoding="utf-8")
        iid = self.db.run("INSERT INTO inbox(uid, message_id, from_addr, subject, body, received_at, status, recorded_at) VALUES (1, '<m@x>', 'marie@cafe.fr', 'Question', 'Bonjour', '2026-11-28T10:00:00', 'new', '2026-11-28T10:00:00')")
        out = run_tool("send_email", self.ctx("support"), {"to": "marie@cafe.fr", "subject": "Re: Question", "body": "Voici",
                                                           "reason": "réponse", "in_reply_to": iid, "attachment": "docs/plaquette.md"})
        self.assertIn("en attente de validation", out)
        a = self.db.pending_approvals()[0]
        self.cfg.workspace.joinpath("docs/plaquette.md").write_text("Plaquette v2 non validée", encoding="utf-8")
        approvals.decide(self.cfg, self.db, a["id"], True)
        self.assertEqual(self.attachment_of(FakeSMTP.sent[0]), b"Plaquette v1")

    # ------------------------------------------------------------ SEC-06 : relecture liée à la version
    def test_delivery_refused_when_file_changed_after_review(self):
        n, rel = self.order_with_report()
        self.review(n, rel)
        (self.cfg.workspace / rel).write_text(REPORT + "\nAjout après relecture.", encoding="utf-8")
        out = self.deliver(n, rel)
        self.assertIn("modifié depuis la relecture", out)
        self.assertEqual(self.db.pending_approvals(), [])
        self.review(n, rel)                                                   # nouvelle relecture de la version finale
        self.assertIn("en attente de validation", self.deliver(n, rel))

    def test_delivery_refused_when_another_file_was_reviewed(self):
        n, rel = self.order_with_report()
        other = rel.replace("rapport.md", "brouillon.md")
        (self.cfg.workspace / other).write_text(REPORT, encoding="utf-8")
        self.review(n, other)
        self.assertIn("n'est pas celui que tu livres", self.deliver(n, rel))

    def test_zip_must_contain_reviewed_version(self):
        from test_quality import site
        n = commerce.test_order(self.cfg, self.db, "Site vitrine", "marie@cafe-lumiere.fr")
        folder = f"livraisons/commande-{n}/site"
        site(self.cfg.workspace / folder)
        self.review(n, f"{folder}/index.html")
        self.assertIn("Archive prête", run_tool("package_files", self.ctx("developpement"), {"folder": folder, "name": "site"}))
        zpath = self.cfg.workspace / f"livraisons/commande-{n}/site.zip"
        good = zpath.read_bytes()
        with zipfile.ZipFile(zpath) as z:                     # archive refaite avec une page d'accueil non relue
            files = {i.filename: z.read(i) for i in z.infolist()}
        index = next(k for k in files if k.endswith("index.html"))
        files[index] = files[index].replace(b"</body>", b"<p>Ajout non relu.</p></body>")
        with zipfile.ZipFile(zpath, "w") as z:
            for k, v in files.items():
                z.writestr(k, v)
        out = self.deliver(n, f"livraisons/commande-{n}/site.zip")
        self.assertIn("ne contient pas la version relue", out)
        zpath.write_bytes(good)
        self.assertIn("en attente de validation", self.deliver(n, f"livraisons/commande-{n}/site.zip"))

if __name__ == "__main__":
    unittest.main()
