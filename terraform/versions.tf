# Verrouillage des versions.
#
# Sans contrainte, un "terraform init" execute dans six mois telechargerait
# une version plus recente du provider, avec potentiellement un comportement
# different. La reproductibilite passe par la.
#
# ~> 6.0 accepte 6.1, 6.2... mais refuse 7.0 (changements incompatibles).

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # Fabrique le zip de deploiement des Lambda a partir du dossier src/,
    # sans etape de build externe.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }
}

# Sources de donnees utilisees dans les politiques IAM, pour eviter d'ecrire
# l'identifiant de compte en dur dans le code.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Cle KMS geree par AWS pour SSM. Necessaire pour autoriser le dechiffrement
# du parametre SecureString contenant la cle API.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}
