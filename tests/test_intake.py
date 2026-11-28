"""Corrections après l'audit interne du PDG : fiche d'offre complète, méthode lisible, collecte des informations du
client et délai qui démarre à leur réception."""
import json
import os
import unittest
import unittest.mock
from pathlib import Path

from test_commerce import Base, FakeIMAP, mail

from ai_studio import commerce
from ai_studio.tools import run_tool


class IntakeTest(Base):
    def setUp(self):
        super().setUp()
        commerce.import_offers(self.db, Path(__file__).resolve().parents[1] / "catalogue" / "offres.json")

    def test_offer_listing_is_explicit(self):
        out = run_tool("list_offers", self.ctx("recherche"), {})
        self.assertIn("Statut : VALIDÉE par le propriétaire", out)
        self.assertIn("Lien de paiement : pas encore créé", out)
        self.assertIn("Marge estimée par vente : Prix 89,00 €", out)
        self.assertIn("Méthode de production : oui", out)
        self.assertIn("après réception des informations complètes", out)
        m = run_tool("offer_method", self.ctx("juridique"), {"offer": "diagnostic seo local"})
        self.assertIn("Score de présence locale", m)
        self.assertIn("Pas de méthode écrite", run_tool("offer_method", self.ctx(), {"offer": "Site vitrine"}))
        self.assertIn("Offre inconnue", run_tool("offer_method", self.ctx(), {"offer": "Autre"}))

    def test_paid_order_asks_information_and_clock_waits(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        s = commerce.get_order(self.db, n)
        self.assertEqual((s["stage"], s["due_at"], s["info_received_at"]), ("attente_client", None, None))
        t = self.db.one("SELECT * FROM tasks WHERE order_ref=? AND agent='support'", (n,))
        from ai_studio.agents import ROLES
        from ai_studio.tools import ToolContext
        ctx = ToolContext(self.cfg, self.db, "support", self.db.task(t["id"]), list(ROLES))
        out = run_tool("send_email", ctx, {"to": "marie@cafe-lumiere.fr", "reason": "infos", "template": "demande_informations",
                                           "fields": {"commande": str(n), "salutation": "Madame Martin"}})
        self.assertIn("en attente de validation", out)
        p = json.loads(self.db.pending_approvals()[-1]["payload"])
        self.assertEqual(p["subject"], "Votre commande Diagnostic SEO local : les informations dont nous avons besoin")
        self.assertIn("----------\nNom et adresse de l'établissement :\nActivité et 3 services principaux :", p["body"])   # formulaire complet
        self.assertIn("1 à 3 concurrents connus (facultatif) :\n----------", p["body"])
        self.assertIn("Le délai de livraison (3 jours) démarre à la réception de ces informations.", p["body"])
        self.assertIn("pas encore reçues", commerce.delivery_brief(self.db, n))
        # la réponse du client ne démarre PAS le délai à elle seule : l'agent vérifie que tout est fourni (audit OPS-02)
        os.environ.update(SMTP_HOST="smtp.gmail.com", SMTP_USER="studio@gmail.com", SMTP_PASSWORD="x")
        imap = FakeIMAP({1: mail("Old <old@x.fr>", "Ancien", "vieux", "<1@x>")})
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)
        imap.msgs[2] = mail("Marie <marie@cafe-lumiere.fr>", "Re: Votre commande", "Café Lumière, 3 rue X, Lyon ; site cafe-lumiere.fr", "<2@x>")
        commerce.sync_inbox(self.cfg, self.db, connect=lambda: imap)
        s = commerce.get_order(self.db, n)
        self.assertEqual((s["stage"], s["due_at"], s["info_received_at"]), ("attente_client", None, None))
        t = self.db.one("SELECT * FROM tasks WHERE title LIKE 'Répondre à Marie%'")
        self.assertIn(f"set_order_status({n}, 'a_livrer'", t["description"])
        self.assertIn("Nom et adresse de l'établissement", t["description"])          # liste de ce qui est attendu
        run_tool("set_order_status", self.ctx("support"), {"order": n, "status": "a_livrer", "note": "toutes les informations reçues"})
        s = commerce.get_order(self.db, n)
        self.assertEqual(s["stage"], "a_livrer"); self.assertIsNotNone(s["info_received_at"]); self.assertIsNotNone(s["due_at"])

    def test_test_order_with_infos_starts_clock_immediately(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr", "Café Lumière, Lyon, cafe-lumiere.fr")
        s = commerce.get_order(self.db, n)
        self.assertEqual(s["stage"], "a_livrer"); self.assertIsNotNone(s["due_at"])
        self.assertIn("Café Lumière", s["owner_notes"])
        self.assertIsNone(self.db.one("SELECT 1 FROM tasks WHERE order_ref=? AND agent='support'", (n,)))

    def test_status_change_starts_clock(self):
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        self.db.run("UPDATE sales SET stage='attente_client' WHERE rowid=?", (n,))
        run_tool("set_order_status", self.ctx("chef_projet"), {"order": n, "status": "a_livrer", "note": "infos reçues par téléphone"})
        self.assertIsNotNone(commerce.get_order(self.db, n)["due_at"])


    def test_reply_asks_only_what_is_missing(self):
        from ai_studio.email_templates import render
        f = {"objet": "Votre proposition", "offre": "Diagnostic SEO local", "reponse": "Oui, le rapport couvre aussi la zone que vous desservez."}
        _, body, _ = render(self.cfg, self.db, "reponse_interesse", f)
        self.assertIn("Nous avons bien toutes les informations", body)               # déjà tout donné : rien à redemander
        self.assertNotIn("Nom et adresse", body)
        _, body, _ = render(self.cfg, self.db, "reponse_interesse", {**f, "infos_manquantes": "toutes"})
        self.assertIn("Nom et adresse de l'établissement", body)
        _, body, _ = render(self.cfg, self.db, "reponse_interesse", {**f, "infos_manquantes": "la ville desservie"})
        self.assertIn("il nous manque seulement quelques éléments.", body); self.assertNotIn("Nom et adresse", body)
        self.assertIn("copiez ce modèle dans votre réponse", body)
        self.assertIn("----------\nLa ville desservie :\n----------", body)                   # formulaire à copier-coller
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        _, body, _ = render(self.cfg, self.db, "demande_informations", {"commande": str(n), "infos_manquantes": "vos 3 services principaux",
                                                                        "remarque": "Nous avons bien votre adresse et votre site."})
        self.assertIn("Nous avons bien votre adresse et votre site.\n\nPour la réaliser, il nous manque seulement quelques éléments.", body)
        self.assertIn("----------\nVos 3 services principaux :\n----------", body)

    def test_order_from_prospect_who_already_wrote(self):
        self.db.run("INSERT INTO inbox(uid, message_id, from_addr, subject, body, status, received_at, recorded_at) "
                    "VALUES (1,'<a@x>','marie@cafe-lumiere.fr','Re: Une piste','Café Lumière, Lyon, cafe-lumiere.fr','replied','2026-11-28T10:00:00+00:00','x')")
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        t = self.db.one("SELECT description FROM tasks WHERE order_ref=? AND agent='support'", (n,))[0]
        self.assertIn("DÉJÀ écrit", t); self.assertIn("#1 du 2026-11-28", t); self.assertIn("ne lui redemande rien", t)


    def test_no_second_email_before_reply(self):
        """Scénario réel du 28/11 : Ventes demande les informations, le client paie, et Support les redemandait."""
        from datetime import timedelta
        from ai_studio import approvals, prospects
        from ai_studio.db import iso, now
        from ai_studio.tools.business import duplicate_contact
        prospects.save(self.db, "marie@cafe-lumiere.fr", "https://cafe-lumiere.fr")
        out = run_tool("send_email", self.ctx("ventes"), {"to": "marie@cafe-lumiere.fr", "subject": "Une piste", "body": "Bonjour", "reason": "r"})
        self.assertIn("en attente", out)
        self.assertIn("attend déjà la validation", run_tool("send_email", self.ctx("support"), {"to": "marie@cafe-lumiere.fr", "subject": "x", "body": "y", "reason": "r"}))
        aid = self.db.pending_approvals()[0]["id"]
        os.environ.update(SMTP_HOST="h", SMTP_USER="u", SMTP_PASSWORD="p")
        with unittest.mock.patch.dict(approvals.EXECUTORS, {"send_email": lambda c, p: "Email envoyé"}):
            approvals.decide(self.cfg, self.db, aid, True)
        self.assertIn("pas encore répondu", duplicate_contact(self.cfg, self.db, "Marie@Cafe-Lumiere.fr"))
        # le client paie avant de répondre : la tâche du Service Client le sait
        n = commerce.test_order(self.cfg, self.db, "Diagnostic SEO local", "marie@cafe-lumiere.fr")
        t = self.db.one("SELECT description FROM tasks WHERE order_ref=? AND agent='support'", (n,))[0]
        self.assertIn("on lui a déjà écrit récemment", t); self.assertIn("n'envoie RIEN", t)
        # dès qu'il répond, on peut lui écrire de nouveau
        self.db.run("INSERT INTO inbox(uid, message_id, from_addr, subject, body, status, received_at, recorded_at) VALUES (9,'<r@x>','marie@cafe-lumiere.fr','Re','ok','queued',?,'x')",
                    (iso(now() + timedelta(seconds=5)),))
        self.assertIsNone(duplicate_contact(self.cfg, self.db, "marie@cafe-lumiere.fr"))
        # et au-delà du délai, même sans réponse
        self.db.run("DELETE FROM inbox"); self.db.run("UPDATE approvals SET decided_at=?", (iso(now() - timedelta(hours=49)),))
        self.assertIsNone(duplicate_contact(self.cfg, self.db, "marie@cafe-lumiere.fr"))


if __name__ == "__main__":
    unittest.main()
